from pathlib import Path

from app.cache import cache_key, input_hash
from app.database import connect, get_cache, import_novel, list_chapters, put_cache
from app.provenance import model_provenance
from app.cache import DEFAULT_PROMPT_VERSION
from app.secrets import decrypt_secret, encrypt_secret
from app.text_processing import detect_and_decode, sha256_text, split_chapters, split_chunks


def test_detect_and_decode_gb18030_chinese():
    raw = "第一章 开始\n少年醒来。".encode("gb18030")
    text, encoding = detect_and_decode(raw)
    assert "少年醒来" in text
    assert encoding in {"gb18030", "gbk"}


def test_split_chapters_common_chinese_titles():
    text = "第一章 初入江湖\n内容一\n第2章 风波起\n内容二"
    chapters = split_chapters(text)
    assert [chapter.title for chapter in chapters] == ["第一章 初入江湖", "第2章 风波起"]
    assert chapters[1].content == "内容二"


def test_split_chunks_is_stable():
    chunks = split_chunks("甲" * 100 + "。" + "乙" * 100, max_chars=80, overlap=10)
    assert len(chunks) >= 2
    assert chunks[0].text_hash == sha256_text(chunks[0].content)


def test_cache_key_includes_required_parts():
    hash_value = input_hash("chapter_summary", "正文")
    key = cache_key(model="gpt-test", task_type="chapter_summary", input_hash_value=hash_value)
    assert ":gpt-test:chapter_summary:" in key
    assert key.endswith(hash_value)


def test_model_provenance_includes_v2_required_fields():
    provenance = model_provenance(
        task_type="chapter_summary",
        model_used="gpt-test",
        source="local_fallback",
        cache_hit=False,
        local_fallback=True,
        model_error=None,
        input_hash_value="abc123",
        cache_key_value="cache-key",
        job_id=42,
    )

    assert provenance == {
        "task_type": "chapter_summary",
        "model_used": "gpt-test",
        "source": "local_fallback",
        "cache_hit": False,
        "local_fallback": True,
        "model_error": None,
        "prompt_version": DEFAULT_PROMPT_VERSION,
        "schema_version": "mvp-json-v1",
        "input_hash": "abc123",
        "cache_key": "cache-key",
        "job_id": 42,
        "provider_call_attempted": False,
        "provider_call_succeeded": False,
    }


def test_secret_encryption_round_trip_hides_plaintext():
    encrypted = encrypt_secret("sk-test")

    assert encrypted.startswith("enc:v")
    assert encrypted != "sk-test"
    assert decrypt_secret(encrypted) == "sk-test"
    assert decrypt_secret("sk-legacy") == "sk-legacy"


def test_import_novel_is_idempotent(tmp_path: Path):
    conn = connect(tmp_path / "test.sqlite3")
    text = "第一章 初入江湖\n内容一\n第二章 风波起\n内容二"
    chapters = split_chapters(text)
    first = import_novel(
        conn,
        title="测试小说",
        source_filename="test.txt",
        encoding="utf-8",
        text_hash=sha256_text(text),
        chapters=chapters,
        chunk_size=20,
    )
    second = import_novel(
        conn,
        title="测试小说",
        source_filename="test.txt",
        encoding="utf-8",
        text_hash=sha256_text(text),
        chapters=chapters,
        chunk_size=20,
    )
    assert first["imported"] is True
    assert second["imported"] is False
    assert len(list_chapters(conn, first["id"])) == 2


def test_model_cache_round_trip(tmp_path: Path):
    conn = connect(tmp_path / "test.sqlite3")
    hash_value = input_hash("qa", "question")
    key = cache_key(model="gpt-test", task_type="qa", input_hash_value=hash_value)
    output = {"status": "ok", "facts": [{"content": "有证据"}]}
    put_cache(conn, key=key, model="gpt-test", task_type="qa", input_hash_value=hash_value, output=output)
    assert get_cache(conn, key) == output
