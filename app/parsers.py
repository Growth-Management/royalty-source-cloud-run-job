from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import re
import unicodedata

from lxml import html as lxml_html
import pandas as pd

from app.drive_client import SourceKind


class ParseValidationError(ValueError):
    pass


@dataclass
class FieldSpec:
    field_name: str
    aliases: tuple[str, ...]
    required: bool = False
    positional_index: int | None = None


@dataclass
class ParsedSheet:
    sheet_name: str
    generic_dataframe: pd.DataFrame
    mapped_dataframe: pd.DataFrame
    header_row_number: int | None
    warnings: list[str]


FIELD_SPECS: dict[SourceKind, tuple[FieldSpec, ...]] = {
    SourceKind.PRODUCT_MASTER: (
        FieldSpec("product_code", ("商品コード", "プロダクトコード"), True, 0),
        FieldSpec("base_isbn", ("基幹ISBN", "基準ISBN"), False, 1),
        FieldSpec("electronic_publication_code", ("電子出版コード", "電子書籍コード"), True, 2),
        FieldSpec("planning_editor", ("企画編集者", "編集担当"), False, 3),
        FieldSpec("sales_start_date", ("販売開始日", "発売日"), False, 4),
        FieldSpec("title", ("書名", "商品名", "タイトル"), True, 5),
        FieldSpec("subtitle", ("副書名", "サブタイトル"), False, 6),
        FieldSpec("price", ("価格", "本体価格", "定価", "基準価格"), False, 7),
        FieldSpec("isbn", ("ISBN",), False, 9),
        FieldSpec("author", ("著者", "著者名"), False, 10),
    ),
    SourceKind.AUTHOR_CONDITIONS: (
        FieldSpec("product_code", ("商品コード", "プロダクトコード"), True, 0),
        FieldSpec("electronic_publication_code", ("電子出版コード", "電子書籍コード"), True, 1),
        FieldSpec("author_identifier_id", ("著者識別ID", "著者ID"), True, 2),
        FieldSpec("title", ("書名", "商品名", "タイトル"), True, 3),
        FieldSpec("planning_editor", ("企画編集者", "編集担当", "企画編集"), False, 4),
        FieldSpec("author_category", ("著者区分", "著者種別"), True, 6),
        FieldSpec("payee_code", ("支払先コード", "支払先CD"), False, 7),
        FieldSpec("author_name", ("著者名", "権利者名"), True, 8),
        FieldSpec("payee_name", ("支払先名",), False, 10),
        FieldSpec("initial_royalty_rate", ("初回印税率", "初期印税率", "印税率"), False, 13),
        FieldSpec("revised_royalty_rate", ("改定印税率", "変更後の印税率"), False, 14),
        FieldSpec("revised_rate_sales_quantity", ("改定部数", "改定販売数", "印税率変更 売上数"), False, 15),
        FieldSpec("revised_rate_sales_amount", ("改定金額", "改定売上額", "印税率変更 売上額"), False, 16),
        FieldSpec("payment_hold_limit_amount", ("支払保留限度額", "支払保留額", "支払留保上限額"), False, 17),
        FieldSpec("withholding_tax_type", ("源泉税区分", "税区分", "源泉徴収種別"), False, 18),
    ),
    SourceKind.EP_STATEMENT_DETAIL: (
        FieldSpec("accounting_month", ("計上年月", "売上年月"), True),
        FieldSpec("billing_code", ("請求先コード", "請求先CD")),
        FieldSpec("billing_name", ("請求先名",)),
        FieldSpec("electronic_publication_code", ("電子出版コード", "電子書籍コード"), True),
        FieldSpec("book_title", ("書名", "タイトル")),
        FieldSpec("list_price", ("定価", "本体価格", "基準価格")),
        FieldSpec("store_name", ("書店名", "販売店名"), True),
        FieldSpec("sales_quantity", ("売上数", "販売数量", "数量", "販売数")),
        FieldSpec("value_code", ("値コード", "バリューコード")),
    ),
    SourceKind.MONTHLY_PRODUCT_SALES: (
        FieldSpec("accounting_month", ("計上年月", "売上年月"), True),
        FieldSpec("product_code", ("商品コード", "プロダクトコード"), True),
        FieldSpec("product_name", ("商品名", "プロダクト名")),
        FieldSpec("sales_company_code", ("販売会社コード", "売上計上会社コード")),
        FieldSpec("sales_company_name", ("販売会社名", "売上計上会社名")),
        FieldSpec("sales_department_code", ("販売部署コード", "売上計上部門コード")),
        FieldSpec("sales_department_name", ("販売部署名", "売上計上部門名")),
        FieldSpec("product_type_code", ("商品区分コード", "プロダクト種別コード")),
        FieldSpec("product_type_name", ("商品区分名", "プロダクト種別名")),
        FieldSpec("bibliographic_title", ("書誌名", "書誌タイトル", "書名")),
        FieldSpec("billing_code", ("請求先コード", "請求先CD")),
        FieldSpec("billing_name", ("請求先名",)),
        FieldSpec("ep_statement_code", ("確報コード", "EP_確報コード")),
        FieldSpec("list_price", ("定価", "本体価格", "基準価格")),
        FieldSpec("sales_quantity", ("売上数", "数量", "販売数")),
        FieldSpec("sales_amount", ("売上額", "金額")),
        FieldSpec("tax_amount", ("税額", "消費税額")),
        FieldSpec("tax_type", ("税区分", "消費税種別")),
        FieldSpec("electronic_publication_code", ("電子出版コード", "電子書籍コード")),
        FieldSpec("partner_company_code", ("取引先会社コード", "パートナー会社コード")),
        FieldSpec("partner_company_name", ("取引先会社名", "パートナー会社名")),
    ),
}


class WorkbookParser:
    def __init__(self, max_generic_columns: int = 40):
        self.max_generic_columns = max_generic_columns

    def parse(self, file_name: str, payload: bytes, source_kind: SourceKind) -> list[ParsedSheet]:
        raw_sheets = self._parse_xlsx(payload) if file_name.lower().endswith(".xlsx") else self._parse_xls(payload)
        parsed = [self._build(source_kind, name, df) for name, df in raw_sheets if not _is_empty(df)]
        if not parsed:
            raise ParseValidationError(f"no non-empty sheets found: {file_name}")
        return parsed

    def _parse_xlsx(self, payload: bytes) -> list[tuple[str, pd.DataFrame]]:
        excel = pd.ExcelFile(BytesIO(payload), engine="openpyxl")
        return [(name, excel.parse(name, dtype=str, header=None)) for name in excel.sheet_names]

    def _parse_xls(self, payload: bytes) -> list[tuple[str, pd.DataFrame]]:
        if _looks_like_html(payload):
            return self._parse_html(payload)
        try:
            excel = pd.ExcelFile(BytesIO(payload), engine="xlrd")
            return [(name, excel.parse(name, dtype=str, header=None)) for name in excel.sheet_names]
        except Exception:
            return self._parse_html(payload)

    def _parse_html(self, payload: bytes) -> list[tuple[str, pd.DataFrame]]:
        document = lxml_html.fromstring(_decode_html(payload))
        tables: list[tuple[str, pd.DataFrame]] = []
        for table_index, table in enumerate(document.xpath("//table"), start=1):
            rows: list[list[str]] = []
            for tr in table.xpath(".//tr"):
                cells = [_clean("".join(cell.itertext())) for cell in tr.xpath("./th|./td")]
                if cells:
                    rows.append(cells)
            if not rows:
                continue
            width = max(len(row) for row in rows)
            normalized_rows = [row + [""] * (width - len(row)) for row in rows]
            tables.append((f"html_table_{table_index}", pd.DataFrame(normalized_rows, dtype=str)))
        return tables

    def _build(self, source_kind: SourceKind, sheet_name: str, dataframe: pd.DataFrame) -> ParsedSheet:
        normalized = dataframe.where(pd.notna(dataframe), "").map(_clean)
        header_index, column_map, warnings = _detect_header(source_kind, normalized)
        return ParsedSheet(
            sheet_name=sheet_name,
            generic_dataframe=self._generic(normalized),
            mapped_dataframe=_mapped(source_kind, normalized, header_index, column_map),
            header_row_number=header_index + 1 if header_index >= 0 else None,
            warnings=warnings,
        )

    def _generic(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for row_index, row in dataframe.iterrows():
            values = [_clean(v) for v in row.tolist()]
            record = {"row_number": int(row_index) + 1, "row_payload_json": json.dumps(values, ensure_ascii=False)}
            for i in range(self.max_generic_columns):
                record[f"col_{i + 1:03d}"] = values[i] if i < len(values) else ""
            rows.append(record)
        return pd.DataFrame(rows)


def _detect_header(source_kind: SourceKind, dataframe: pd.DataFrame) -> tuple[int, dict[str, int], list[str]]:
    specs = FIELD_SPECS[source_kind]
    aliases = {_norm(alias): spec.field_name for spec in specs for alias in spec.aliases}
    best_index, best_map = None, {}
    for row_index in range(min(len(dataframe), 30)):
        current = {}
        for col_index, value in enumerate(dataframe.iloc[row_index].tolist()):
            field = aliases.get(_norm(value))
            if field:
                current.setdefault(field, col_index)
        if len(current) > len(best_map):
            best_index, best_map = row_index, current
    required = {spec.field_name for spec in specs if spec.required}
    if best_index is None or not required <= set(best_map):
        positional = {spec.field_name: spec.positional_index for spec in specs if spec.positional_index is not None}
        if required <= set(positional):
            return -1, positional, [f"headerless positional mapping applied for {source_kind.value}"]
        missing = sorted(required - set(best_map))
        raise ParseValidationError(f"required columns missing for {source_kind.value}: {', '.join(missing)}")
    optional = {spec.field_name for spec in specs if not spec.required}
    warnings = [f"optional column missing for {source_kind.value}: {field}" for field in sorted(optional - set(best_map))]
    return best_index, best_map, warnings


def _mapped(source_kind: SourceKind, dataframe: pd.DataFrame, header_index: int, column_map: dict[str, int]) -> pd.DataFrame:
    records = []
    for row_index in range(header_index + 1, len(dataframe)):
        row = dataframe.iloc[row_index]
        record = {"row_number": row_index + 1}
        has_value = False
        for spec in FIELD_SPECS[source_kind]:
            col_index = column_map.get(spec.field_name)
            value = _clean(row.iloc[col_index]) if col_index is not None and col_index < len(row) else ""
            record[spec.field_name] = value
            has_value = has_value or bool(value)
        if has_value:
            records.append(record)
    return pd.DataFrame(records)


def _looks_like_html(payload: bytes) -> bool:
    head = payload[:512].lstrip().lower()
    return head.startswith(b"<html") or b"<table" in head


def _decode_html(payload: bytes) -> str:
    for encoding in ("cp932", "utf-8", "shift_jis"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            pass
    return payload.decode("utf-8", errors="ignore")


def _is_empty(dataframe: pd.DataFrame) -> bool:
    return dataframe.empty or not dataframe.where(pd.notna(dataframe), "").map(_clean).map(bool).any().any()


def _clean(value: object) -> str:
    text = "" if value is None else str(value).replace("\u3000", " ").strip()
    return "" if text.lower() == "nan" else text


def _norm(value: object) -> str:
    return re.sub(r"[\s_\-()/（）・.]", "", unicodedata.normalize("NFKC", _clean(value)).lower())
