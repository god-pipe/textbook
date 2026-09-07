"""
report/generate_report.py
----------------------------
把 AI 分析結果 (ReportAnalysis) + 各來源執行摘要 + Twitch Pulse + Meme Pulse
+ 本地品牌素材，渲染成「Creator Intelligence Magazine」風格的 Static HTML。

v2 改版重點：
- 不再是「文字塞進深色 card」，而是 hero story / featured 兩欄 / 一般
  雜誌卡片的分層排版（tier 由 ai/schema.py 依排序位置指定）。
- 圖片一律用「真實資料」：YouTube 用 hero_image_url(maxresdefault) 搭配
  image_url(API thumbnail) 當 <img onerror> fallback；其他來源只用
  image_url，取不到就顯示「NO IMAGE AVAILABLE」placeholder，絕不猜測
  或使用不相關圖片。
- 來源一律做成可點擊的 `<a>` 按鈕（依平台顯示對應文案），不再出現
  裸 URL 或看不懂的 `[1]`。
- 本地 assets/ 資料夾的 logo、分類小圖示會被自動收進來（見
  report/assets.py），且都限制在小尺寸的裝飾性位置。
- 新增 Twitch Pulse（純結構化數字，不經 AI）與 Meme Pulse（獨立側欄）兩個
  額外區塊，並在報告上標示每個事件的來源可信度（wire_service /
  established_media / community / unverified）。
- 若這份分析是用測試層級模型（Ollama 免費模型）跑的，報告最上方會顯示
  醒目警告。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ai.schema import ReportAnalysis, analysis_to_dict
from config import ReportConfig, ASSETS_DIR
from data_sources.base import SourceRunResult, RawItem
from data_sources.twitch_source import WeeklyTwitchPulse
from report.assets import collect_report_assets

TEMPLATE_DIR = Path(__file__).parent / "templates"

CATEGORY_EMOJI = {
    "Trending": "🔥",
    "Creator News": "👤",
    "YouTube": "🎥",
    "Streaming": "🎮",
    "Japan x US": "🌏",
    "AI": "🤖",
    "Controversy": "🚨",
}

PLATFORM_BADGE = {
    "youtube": {"label": "YouTube", "symbol": "▶"},
    "twitch": {"label": "Twitch", "symbol": "🟣"},
    "reddit": {"label": "Reddit", "symbol": "👽"},
    "crawl4ai": {"label": "News", "symbol": "📰"},
}

SOURCE_CTA = {
    "youtube": "Watch on YouTube ↗",
    "twitch": "Watch Clip on Twitch ↗",
    "reddit": "View on Reddit ↗",
    "crawl4ai": "Read Source ↗",
}

TRUST_BADGE = {
    "wire_service": "✓ Wire Service",
    "established_media": "✓ Verified Media",
    "community": "Community Source",
    "unverified": None,  # 不特別標示，避免「沒標籤」看起來像扣分
}


def _date_range_label(start: date, end: date) -> str:
    return f"{start.isoformat()} — {end.isoformat()}"


def _cover_date_label(start: date, end: date) -> str:
    """雜誌封面用的精簡日期格式，例如 2026.08.30 — 09.06。"""
    start_str = start.strftime("%Y.%m.%d")
    if start.year == end.year:
        end_str = end.strftime("%m.%d")
    else:
        end_str = end.strftime("%Y.%m.%d")
    return f"{start_str} — {end_str}"


def _initials(name: str) -> str:
    name = (name or "").strip()
    if not name or name.lower() == "unknown creator":
        return "?"
    return name[0].upper()


def _prepare_event(ev: dict) -> dict:
    """幫每個事件補上模板方便直接用的欄位：圖片 primary/fallback、
    平台徽章、來源按鈕文案、分類 emoji、Creator 姓名縮寫、來源可信度徽章。
    全部是純粹的顯示邏輯，不影響底層資料。"""
    hero_img = ev.get("hero_image_url")
    img = ev.get("image_url")

    if hero_img:
        ev["img_primary"] = hero_img
        ev["img_fallback"] = img if (img and img != hero_img) else ""
    elif img:
        ev["img_primary"] = img
        ev["img_fallback"] = ""
    else:
        ev["img_primary"] = ""
        ev["img_fallback"] = ""

    source_type = ev.get("source_type", "")
    ev["platform_badge"] = PLATFORM_BADGE.get(source_type, {"label": source_type.title() or "Source", "symbol": "🔗"})
    ev["source_cta"] = SOURCE_CTA.get(source_type, "View Source ↗")
    ev["category_emoji"] = CATEGORY_EMOJI.get(ev.get("category", ""), "📰")
    ev["creator_initial"] = _initials(ev.get("creator", ""))
    ev["trust_badge"] = TRUST_BADGE.get(ev.get("source_trust", "unverified"))
    return ev


def _prepare_twitch_pulse(pulse: WeeklyTwitchPulse | None) -> dict | None:
    if pulse is None or not pulse.ok:
        return None
    return {
        "collected_at": pulse.collected_at.strftime("%Y-%m-%d %H:%M"),
        "scope_note": pulse.scope_note,
        "top_games": [{"name": g.name, "viewer_count": g.viewer_count, "box_art_url": g.box_art_url}
                      for g in pulse.top_games[:8]],
        "top_streams_en": [{"broadcaster_name": s.broadcaster_name, "game_name": s.game_name,
                             "viewer_count": s.viewer_count, "title": s.title, "url": s.url,
                             "thumbnail_url": s.thumbnail_url}
                            for s in pulse.top_streams_en[:8]],
        "top_clip": ({"title": pulse.top_clip.title, "url": pulse.top_clip.url,
                      "broadcaster_name": pulse.top_clip.broadcaster_name,
                      "game_name": pulse.top_clip.game_name, "view_count": pulse.top_clip.view_count,
                      "thumbnail_url": pulse.top_clip.thumbnail_url}
                     if pulse.top_clip else None),
        "top_clips_leaderboard": [{"title": c.title, "url": c.url, "broadcaster_name": c.broadcaster_name,
                                    "game_name": c.game_name, "view_count": c.view_count,
                                    "thumbnail_url": c.thumbnail_url}
                                   for c in pulse.top_clips_leaderboard[:8]],
    }


def _prepare_meme_items(items: list[RawItem] | None) -> list[dict]:
    if not items:
        return []
    # 依 upvotes 排序，只取前幾筆給側欄用，避免佔用太多版面
    sorted_items = sorted(items, key=lambda it: it.metrics.get("upvotes", 0), reverse=True)
    out = []
    for it in sorted_items[:6]:
        out.append({
            "title": it.title,
            "url": it.url,
            "subreddit": it.extra.get("subreddit", ""),
            "upvotes": it.metrics.get("upvotes", 0),
            "num_comments": it.metrics.get("num_comments", 0),
            "image_url": it.image_url,
            "author": it.author,
        })
    return out


def render_report_html(analysis: ReportAnalysis, report_cfg: ReportConfig,
                        start: date, end: date,
                        source_results: list[SourceRunResult],
                        output_dir: Path | None = None,
                        twitch_pulse: WeeklyTwitchPulse | None = None,
                        meme_items: list[RawItem] | None = None) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report_template.html")

    period_label_full = report_cfg.period_label()
    period_label_line1 = period_label_full.split("\n")[0]

    analysis_dict = analysis_to_dict(analysis)
    all_events = [_prepare_event(e) for e in analysis_dict["top_events"]]
    for i, e in enumerate(all_events, start=1):
        e["rank"] = i

    hero_event = all_events[0] if len(all_events) > 0 else None
    featured_events = all_events[1:3]
    card_events = all_events[3:]

    # 封面副標：列出這期出現過的分類（去重、最多 4 個），沒有就用預設文案
    seen_categories = []
    for e in all_events:
        cat = e.get("category")
        if cat and cat not in seen_categories:
            seen_categories.append(cat)
    cover_tagline = " • ".join(seen_categories[:4]) if seen_categories else "STREAMING • YOUTUBE • CREATOR CULTURE"

    # 本地品牌素材（logo/分類圖示），輸出目錄還沒建立時先不複製，等 save_report 時再收集，
    # 這裡先給模板一個空殼避免 None 造成 Jinja 出錯。
    assets = {"logo": None, "favicon": None, "category_icons": {}}
    if output_dir is not None:
        assets = collect_report_assets(ASSETS_DIR, output_dir)

    # 分類圖示：如果本地 assets 有對應圖示，補進每個事件方便模板判斷
    for e in all_events:
        e["category_icon_path"] = assets["category_icons"].get(e.get("category"))

    html = template.render(
        period_label=period_label_full,
        period_label_line1=period_label_line1,
        date_range_label=_date_range_label(start, end),
        cover_date_label=_cover_date_label(start, end),
        cover_tagline=cover_tagline,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        language=report_cfg.language,
        analysis=analysis_dict,
        hero_event=hero_event,
        featured_events=featured_events,
        card_events=card_events,
        pulse=analysis_dict["pulse"],
        source_summaries=[r.summary() for r in source_results],
        assets=assets,
        twitch_pulse=_prepare_twitch_pulse(twitch_pulse),
        meme_items=_prepare_meme_items(meme_items),
        is_test_tier=analysis.is_test_tier,
        ai_provider_used=analysis.ai_provider_used,
        ai_model_used=analysis.ai_model_used,
    )
    return html


def save_report(html_or_render_fn, report_cfg: ReportConfig, start: date, end: date,
                 output_dir: Path, analysis: ReportAnalysis | None = None,
                 source_results: list[SourceRunResult] | None = None,
                 twitch_pulse: WeeklyTwitchPulse | None = None,
                 meme_items: list[RawItem] | None = None) -> Path:
    """
    儲存報告。因為 assets 需要先知道輸出目錄才能複製過去，這裡採用
    「先建立輸出目錄 -> 收集 assets -> 再渲染 HTML」的順序。

    為了不破壞既有呼叫方式，這個函式接受兩種用法：
    1) save_report(html_string, report_cfg, start, end, output_dir)
       —— 舊用法，直接存已經渲染好的 HTML（不會有 assets）。
    2) save_report(None, report_cfg, start, end, output_dir,
                    analysis=analysis, source_results=source_results, ...)
       —— 新用法，函式自己完成「建目錄 -> 收 assets -> 渲染」全流程。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report_cfg.report_type.lower()}_{start.isoformat()}_{end.isoformat()}.html"
    out_path = output_dir / filename

    if isinstance(html_or_render_fn, str):
        html = html_or_render_fn
    else:
        if analysis is None or source_results is None:
            raise ValueError("新用法需要提供 analysis 與 source_results")
        html = render_report_html(analysis, report_cfg, start, end, source_results, output_dir=output_dir,
                                   twitch_pulse=twitch_pulse, meme_items=meme_items)

    out_path.write_text(html, encoding="utf-8")
    return out_path
