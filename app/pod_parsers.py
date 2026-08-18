from __future__ import annotations

from io import BytesIO, StringIO
import json
import re
import unicodedata

import pandas as pd

from app.drive_client import SourceKind
from app.parsers import ParsedSheet, ParseValidationError


POD_COLUMNS = (
    "publisher",
    "product_code",
    "isbn",
    "title",
    "unit_price",
    "rate",
    "quantity",
    "net_amount",
    "tax",
    "sales_amount",
    "manufacturing_cost",
    "factor_15",
    "factor_108",
    "pages",
    "dl_month",
    "sales_month",
)

PF_COLUMNS = (
    "category",
    "publisher_id",
    "isbn",
    "title",
    "sales_month",
    "store",
    "unit_price",
    "quantity",
    "distribution_rate",
    "sales_amount",
    "sales_fee",
    "printing_unit_cost",
    "printing_cost",
    "exchange_rate",
    "payment_status",
    "payment_amount",
)


class PodParser:
    def __init__(self, max_generic_columns: int = 40):
        self.max_generic_columns = max_generic_columns

    def parse(
        self,
        file_name: str,
        payload: bytes,
        source_kind: SourceKind,
        target_month: str,
    ) -> list[ParsedSheet]:
        if source_kind == SourceKind.PF_SALES_REPORT:
            dataframe = self._parse_csv(payload)
            mapped = self._map_pf(dataframe, target_month)
            return [self._sheet("csv", dataframe, mapped)]

        if source_kind in (SourceKind.AMAZON_POD_MONTHLY, SourceKind.POD_ACCESS_HISTORY):
            return self._parse_access_compatible_excel(payload, target_month, source_kind)

        raise ParseValidationError(f"unsupported POD source kind: {source_kind.value}")

    def _parse_csv(self, payload: bytes) -> pd.DataFrame:
        last_error: Exception | None = None
        for encoding in ("cp932", "utf-8-sig", "utf-8", "shift_jis"):
            try:
                text = payload.decode(encoding)
                return pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
            except (UnicodeDecodeError, pd.errors.ParserError) as exc:
                last_error = exc
        raise ParseValidationError(f"PF CSV decode failed: {last_error}")

    def _map_pf(self, dataframe: pd.DataFrame, target_month: str) -> pd.DataFrame:
        aliases = {
            "category": ("種別",),
            "publisher_id": ("書店ID", "出版社ID"),
            "isbn": ("ISBN",),
            "title": ("書籍名", "タイトル"),
            "sales_month": ("販売月",),
            "store": ("ストア",),
            "unit_price": ("販売価格（税別）", "販売価格(税別)"),
            "quantity": ("販売数",),
            "distribution_rate": ("分配率",),
            "sales_amount": ("売上金額（税別）", "売上金額(税別)"),
            "sales_fee": ("販売手数料（税別）", "販売手数料(税別)"),
            "printing_unit_cost": ("印刷費単価（税別）", "印刷費単価(税別)"),
            "printing_cost": ("印刷費（税別）", "印刷費(税別)"),
            "exchange_rate": ("為替レート",),
            "payment_status": ("支払状況",),
            "payment_amount": ("支払額（売上-手数料-印刷費）", "支払額(売上-手数料-印刷費)", "支払額"),
        }
        normalized_columns = {_norm(column): column for column in dataframe.columns}
        selected: dict[str, pd.Series] = {}
        missing: list[str] = []
        for field, names in aliases.items():
            source = next((normalized_columns.get(_norm(name)) for name in names if normalized_columns.get(_norm(name))), None)
            if source is None:
                missing.append(field)
                selected[field] = pd.Series([""] * len(dataframe), index=dataframe.index, dtype=str)
            else:
                selected[field] = dataframe[source].astype(str).map(_clean)
        required = {"isbn", "sales_month", "unit_price", "quantity", "sales_amount", "sales_fee", "printing_cost"}
        absent_required = sorted(required & set(missing))
        if absent_required:
            raise ParseValidationError(f"required PF columns missing: {', '.join(absent_required)}")
        mapped = pd.DataFrame(selected)
        normalized_month = mapped["sales_month"].map(_month)
        mapped = mapped.loc[normalized_month == target_month].copy()
        mapped.insert(0, "row_number", mapped.index.astype(int) + 2)
        if mapped.empty:
            raise ParseValidationError(f"PF CSV has no rows for target_month={target_month}")
        return mapped.reset_index(drop=True)

    def _parse_access_compatible_excel(
        self,
        payload: bytes,
        target_month: str,
        source_kind: SourceKind,
    ) -> list[ParsedSheet]:
        excel = pd.ExcelFile(BytesIO(payload), engine="openpyxl")
        result: list[ParsedSheet] = []
        for sheet_name in excel.sheet_names:
            raw = excel.parse(sheet_name, dtype=str, header=None).fillna("")
            header_index = self._find_access_header(raw)
            if header_index is None:
                continue
            header = [_norm(value) for value in raw.iloc[header_index].tolist()]
            index_by_name = {name: idx for idx, name in enumerate(header) if name}
            aliases = {
                "publisher": ("発行社",),
                "product_code": ("プロダクトコード", "商品コード"),
                "isbn": ("isbn",),
                "title": ("タイトル", "書名"),
                "unit_price": ("単価",),
                "rate": ("掛率",),
                "quantity": ("冊数", "販売数"),
                "net_amount": ("正味金額",),
                "tax": ("消費税",),
                "sales_amount": ("売上金額",),
                "manufacturing_cost": ("製造原価",),
                "factor_15": ("15", "1.5"),
                "factor_108": ("108",),
                "pages": ("頁数", "ページ数"),
                "dl_month": ("dl年月", "dl月"),
                "sales_month": ("年月", "販売月"),
            }
            pages_index = next(
                (index_by_name.get(_norm(name)) for name in aliases["pages"] if _norm(name) in index_by_name),
                None,
            )
            positional_fallback = {
                "dl_month": pages_index + 1 if pages_index is not None else None,
                "sales_month": pages_index + 2 if pages_index is not None else None,
            }
            rows: list[dict[str, str | int]] = []
            for row_index in range(header_index + 1, len(raw)):
                row = raw.iloc[row_index].tolist()
                record: dict[str, str | int] = {"row_number": row_index + 1}
                for field, names in aliases.items():
                    col_index = next((index_by_name.get(_norm(name)) for name in names if _norm(name) in index_by_name), None)
                    if col_index is None and field in positional_fallback:
                        col_index = positional_fallback[field]
                    record[field] = _clean(row[col_index]) if col_index is not None and col_index < len(row) else ""
                if not any(str(record.get(field, "")).strip() for field in ("product_code", "isbn", "title", "quantity", "sales_amount")):
                    continue
                if source_kind == SourceKind.AMAZON_POD_MONTHLY and _month(record.get("sales_month", "")) != target_month:
                    continue
                rows.append(record)
            if not rows:
                continue
            mapped = pd.DataFrame(rows)
            result.append(self._sheet(sheet_name, raw, mapped, header_index + 1))
        if not result:
            raise ParseValidationError(f"no POD sales rows found for target_month={target_month}")
        return result

    def _find_access_header(self, dataframe: pd.DataFrame) -> int | None:
        for index in range(min(50, len(dataframe))):
            values = {_norm(value) for value in dataframe.iloc[index].tolist()}
            if "isbn" in values and ("冊数" in values or "販売数" in values) and ("売上金額" in values or "正味金額" in values):
                return index
        return None

    def _sheet(self, name: str, raw: pd.DataFrame, mapped: pd.DataFrame, header_row_number: int | None = 1) -> ParsedSheet:
        return ParsedSheet(
            sheet_name=name,
            generic_dataframe=self._generic(raw),
            mapped_dataframe=mapped,
            header_row_number=header_row_number,
            warnings=[],
        )

    def _generic(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        normalized = dataframe.fillna("")
        rows = []
        for row_index, row in normalized.iterrows():
            values = [_clean(value) for value in row.tolist()]
            record = {
                "row_number": int(row_index) + 1,
                "row_payload_json": json.dumps(values, ensure_ascii=False),
            }
            for column_index in range(self.max_generic_columns):
                record[f"col_{column_index + 1:03d}"] = values[column_index] if column_index < len(values) else ""
            rows.append(record)
        return pd.DataFrame(rows)


def _clean(value: object) -> str:
    text = "" if value is None else str(value).replace("\u3000", " ").strip()
    return "" if text.lower() in ("nan", "none") else text


def _norm(value: object) -> str:
    return re.sub(r"[\s_\-()/（）・.]", "", unicodedata.normalize("NFKC", _clean(value)).lower())


def _month(value: object) -> str:
    text = unicodedata.normalize("NFKC", _clean(value))
    match = re.search(r"(20\d{2})\D*([01]?\d)", text)
    if not match:
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 6 and digits[:4].startswith("20"):
            return digits[:6]
        return ""
    month = int(match.group(2))
    return f"{match.group(1)}{month:02d}" if 1 <= month <= 12 else ""
