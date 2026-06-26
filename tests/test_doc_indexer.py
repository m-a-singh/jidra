from jidra.doc_indexer import _CHUNK_MAX_CHARS, _split_markdown


def test_split_markdown_paragraph_fallback_for_oversized_section():
    # Single H2 section whose body exceeds _CHUNK_MAX_CHARS must be sub-split
    # into multiple chunks, each still attributed to the same heading.
    paragraphs = [f"Paragraph {i} " + ("word " * 200) for i in range(5)]
    body = "\n\n".join(paragraphs)
    assert len(body) > _CHUNK_MAX_CHARS

    text = "## Big Section\n" + body

    chunks = _split_markdown(text)

    assert len(chunks) > 1
    for heading, content in chunks:
        assert heading == "Big Section"
        assert content.strip()

    # No content lost: every paragraph's distinctive marker must appear
    # somewhere across the resulting chunks.
    joined = "\n\n".join(c for _, c in chunks)
    for i in range(5):
        assert f"Paragraph {i} " in joined


def test_split_markdown_small_section_not_split():
    text = "## Small Section\nJust a short paragraph."
    chunks = _split_markdown(text)
    assert len(chunks) == 1
    assert chunks[0] == ("Small Section", "Just a short paragraph.")
