"""客户文档资料类型（doc_categories 配置）纯函数测试。"""
import json

import pytest

from app.services.doc_categories import (
    DEFAULT_DOC_CATEGORIES,
    MAX_CATEGORIES,
    parse_doc_categories,
    validate_categories_payload,
)


class TestParseDocCategories:
    def test_none_falls_back_to_default(self):
        assert parse_doc_categories(None) == DEFAULT_DOC_CATEGORIES

    def test_dirty_json_falls_back_to_default(self):
        assert parse_doc_categories("{not json") == DEFAULT_DOC_CATEGORIES

    def test_empty_list_falls_back_to_default(self):
        assert parse_doc_categories("[]") == DEFAULT_DOC_CATEGORIES

    def test_filters_missing_fields_and_duplicates(self):
        raw = json.dumps([
            {"value": "a", "label": "甲"},
            {"value": "a", "label": "重复"},
            {"value": "b"},  # 缺 label，过滤
            {"label": "缺value"},  # 缺 value，过滤
        ])
        assert parse_doc_categories(raw) == [{"value": "a", "label": "甲"}]


class TestValidateCategoriesPayload:
    def test_ok(self):
        items = [{"value": "factsheet", "label": " 产品资料 "}]
        assert validate_categories_payload(items) == [{"value": "factsheet", "label": "产品资料"}]

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            validate_categories_payload([])

    def test_bad_value_pattern(self):
        with pytest.raises(ValueError):
            validate_categories_payload([{"value": "Bad-Value", "label": "x"}])

    def test_missing_label(self):
        with pytest.raises(ValueError):
            validate_categories_payload([{"value": "ok", "label": "  "}])

    def test_duplicate_value(self):
        with pytest.raises(ValueError):
            validate_categories_payload([
                {"value": "a", "label": "1"},
                {"value": "a", "label": "2"},
            ])

    def test_over_limit(self):
        items = [{"value": f"t{i}", "label": f"T{i}"} for i in range(MAX_CATEGORIES + 1)]
        with pytest.raises(ValueError):
            validate_categories_payload(items)
