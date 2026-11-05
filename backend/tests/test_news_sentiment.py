import pandas as pd

from app.providers.news import SNIPPET_LEN, clean_snippet, parse_rss
from app.services import news, sentiment


def test_lexicon_polarity_and_negation():
    assert sentiment.lexicon_score("Nvidia beats estimates and raises guidance") > 0.3
    assert sentiment.lexicon_score("Shares plunge after the company misses and cuts outlook") < -0.3
    assert sentiment.lexicon_score("Company does not expect growth") < 0
    assert sentiment.lexicon_score("Company schedules annual meeting") == 0


def test_score_texts_returns_labels():
    scores, scorer = sentiment.score_texts(["Stock surges to record", "Stock tumbles on weak demand"])
    assert scorer in ("lexicon", "finbert")
    assert scores[0][1] == "positive" and scores[1][1] == "negative"


def test_spike_detection_vs_baseline():
    days = pd.date_range("2026-01-01", periods=20, freq="D")
    mean = pd.Series([0.05, -0.05] * 9 + [0.05, 0.8], index=days)
    count = pd.Series([4] * 20, index=days)
    spikes = news.detect_spikes(mean, count)
    assert [s["date"] for s in spikes] == ["2026-01-20"]
    assert spikes[0]["direction"] == "positive" and spikes[0]["z"] > 2


def test_spike_requires_enough_headlines():
    days = pd.date_range("2026-01-01", periods=20, freq="D")
    mean = pd.Series([0.0] * 19 + [0.9], index=days)
    count = pd.Series([4] * 19 + [1], index=days)
    assert news.detect_spikes(mean, count) == []


def test_snippets_are_short_and_html_free():
    s = clean_snippet("<p>" + "word " * 200 + "</p>")
    assert len(s) <= SNIPPET_LEN and "<" not in s


def test_rss_parsing_keeps_only_headline_snippet_link():
    xml = """<?xml version="1.0"?><rss><channel><item><title>Apple beats - Reuters</title>
    <link>https://example.com/a</link><pubDate>Tue, 03 Nov 2026 12:00:00 GMT</pubDate>
    <description>&lt;b&gt;Long&lt;/b&gt; article text</description><source url="https://reuters.com">Reuters</source></item></channel></rss>"""
    items = parse_rss(xml, "AAPL", "google-news-rss")
    assert items[0].headline == "Apple beats" and items[0].source == "Reuters"
    assert items[0].url == "https://example.com/a" and items[0].published_at.startswith("2026-11-03")


def test_news_api_and_storage(client):
    d = client.get("/api/ticker/AAPL/news").json()
    assert d["items"], "sample headlines expected"
    for it in d["items"]:
        assert it["url"].startswith("http") and len(it["snippet"] or "") <= SNIPPET_LEN
        assert -1 <= it["sentiment"] <= 1
    s = client.get("/api/ticker/AAPL/sentiment?days=30").json()
    assert s["points"] and "spikes" in s
    summ = client.post("/api/ticker/AAPL/news/summary").json()
    assert summ.get("needs_key") is True  # no OPENAI_API_KEY in tests -> friendly message, no crash


def test_crowd_api(client):
    c = client.get("/api/ticker/NVDA/crowd").json()
    assert "unverified" in c["label"]
    assert c["posts"] and c["mentions"]
