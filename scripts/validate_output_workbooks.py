#!/usr/bin/env python3
"""Validate generated workbooks against their BigQuery source tables."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
