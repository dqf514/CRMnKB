from app.services.ai_tasks import parse_todos


def test_parse_valid_json_array():
    raw = '[{"title": "发送报价单", "due_date": "2026-08-20", "priority": "high"}]'
    todos = parse_todos(raw)
    assert len(todos) == 1
    assert todos[0]["title"] == "发送报价单"
    assert todos[0]["priority"] == "high"
    # 统一存 naive UTC（Task.due_date 为不带时区的 TIMESTAMP 列；aware 会触发 asyncpg 报错）
    assert todos[0]["due_date"].tzinfo is None


def test_parse_with_surrounding_text():
    raw = '好的，提取结果如下：\n[{"title": "回访客户"}]\n以上。'
    todos = parse_todos(raw)
    assert [t["title"] for t in todos] == ["回访客户"]


def test_parse_empty_array():
    assert parse_todos("[]") == []


def test_parse_invalid_json_returns_empty():
    assert parse_todos("这不是 JSON") == []
    assert parse_todos("[{title: 缺少引号}]") == []
    assert parse_todos('{"title": "不是数组"}') == []


def test_parse_filters_invalid_items():
    raw = '[{"title": "有效"}, {"no_title": true}, "字符串", {"title": ""}]'
    todos = parse_todos(raw)
    assert [t["title"] for t in todos] == ["有效"]


def test_parse_drops_invalid_priority_and_date():
    raw = '[{"title": "任务", "priority": "urgent", "due_date": "不是日期"}]'
    todos = parse_todos(raw)
    assert todos[0].get("priority") is None
    assert todos[0].get("due_date") is None
