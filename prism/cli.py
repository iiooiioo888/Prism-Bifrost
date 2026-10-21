"""稜鏡命令列。神話名只出現在命令分組，不進入資料表。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from sqlalchemy import select

from prism.db.models import AlertRow, CanonicalEvent, EventAssessment, EventMention, MergeSuggestion
from prism.db.session import session_scope
from prism.commerce.images import ProductImageService
from prism.commerce.service import CommerceError, CommerceService
from prism.raven.compliance import ComplianceError
from prism.raven.images import ImageCrawlError
from prism.raven.proxies import AccessDenied, ProxyPoolExhausted
from prism.demo.seed import seed
from prism.herald.brief import BriefService
from prism.horn.channels import test_channels
from prism.raven.probes.gated import LockedProbe
from prism.raven.probes.rss import RssProbe
from prism.runtime import PrismRuntime
from prism.watch.service import WatchService

app = typer.Typer(help="稜鏡 Prism Bifröst", no_args_is_help=True)
raven_app = typer.Typer(help="福金與霧尼：探針健康與單次測試", no_args_is_help=True)
bridge_app = typer.Typer(help="彩虹橋基座：採集", no_args_is_help=True)
weave_app = typer.Typer(help="彩虹橋橋身：規範事件", no_args_is_help=True)
weave_event = typer.Typer(help="事件", no_args_is_help=True)
watch_app = typer.Typer(help="海姆達爾：告警與評分", no_args_is_help=True)
herald_app = typer.Typer(help="伊里斯：簡報與複核", no_args_is_help=True)
horn_app = typer.Typer(help="加拉爾號角：告警通道", no_args_is_help=True)
demo_app = typer.Typer(help="驗收資料", no_args_is_help=True)
scene_app = typer.Typer(help="場景配置與決策", no_args_is_help=True)

app.add_typer(raven_app, name="raven")
app.add_typer(bridge_app, name="bridge")
app.add_typer(weave_app, name="weave")
weave_app.add_typer(weave_event, name="event")
app.add_typer(watch_app, name="watch")
app.add_typer(herald_app, name="herald")
app.add_typer(horn_app, name="horn")
app.add_typer(demo_app, name="demo")
app.add_typer(scene_app, name="scene")


def _runtime() -> PrismRuntime:
    return PrismRuntime()


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]
    rendered = [[str(cell) for cell in row] for row in rows]
    for row in rendered:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    typer.echo(line)
    typer.echo("  ".join("-" * width for width in widths))
    for row in rendered:
        typer.echo("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))


@raven_app.command("status")
def raven_status() -> None:
    """探針健康度。未啟用與降級都會顯式列出。"""
    runtime = _runtime()
    rows = []
    for item in runtime.configured_probe_status():
        rows.append(
            [
                item["probe_id"],
                item["status"],
                f"{float(item['fallback_ratio_24h']):.2f}",
                str(item["consecutive_failures"]),
                item["last_success_at"] or "—",
                item["detail"],
                "—" if item.get("degradation_rate") is None else f"{float(item['degradation_rate']):.2f}",
                "是" if item.get("preempt_fallback") else "否",
            ]
        )
    _print_table(["探針", "狀態", "備選比例", "連續失敗", "最近成功", "說明", "劣化", "提前備選"], rows)


@raven_app.command("proxies")
def raven_proxies() -> None:
    """出口代理池。目標拒絕時不會改用其他 IP。"""
    runtime = _runtime()
    status = runtime.configured_proxy_status()
    typer.echo(f"代理池啟用：{'是' if status['enabled'] else '否'}，拒絕後換 IP：否")
    rows = []
    for item in status["items"]:
        rows.append(
            [
                item["proxy_id"],
                item["status"],
                str(item["consecutive_transport_failures"]),
                item["last_outcome"] or "—",
                item["detail"],
            ]
        )
    _print_table(["代理", "狀態", "連續傳輸失敗", "最近結果", "說明"], rows)


@raven_app.command("images")
def raven_images(
    sku: str = typer.Option(..., "--sku", help="標準化 SKU"),
    url: str = typer.Option(..., "--url", help="已允許網域上的公開商品頁"),
    scene: str = typer.Option("double11", "--scene", help="場景"),
) -> None:
    """抓取頁面上標成商品圖的公開圖片。不跟隨重新導向，目標拒絕時不換 IP。"""
    runtime = _runtime()
    try:
        result = ProductImageService(runtime).crawl(scene_id=scene, sku_code=sku, page_url=url)
    except (CommerceError, ComplianceError, AccessDenied, ImageCrawlError, ProxyPoolExhausted) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if result["note"]:
        typer.echo(result["note"])
    for item in result["images"]:
        typer.echo(f"已保存 {item['byte_size']} bytes  {item['content_type']}  {item['image_url']}")
    for item in result["rejected"]:
        typer.echo(f"未保存 {item['url']}：{item['reason']}")


@raven_app.command("probe")
def raven_probe(platform: str) -> None:
    """單次探針測試。"""
    runtime = _runtime()
    probes = runtime.settings.raven.get("probes", {})
    spec = probes.get(platform)
    if spec is None:
        typer.echo(f"未知探針：{platform}")
        raise typer.Exit(code=1)
    if not spec.get("enabled", False):
        reason = spec.get("locked_reason") or "通道未啟用"
        locked = LockedProbe(platform, platform, reason)
        health = asyncio.run(locked.health())
        typer.echo(f"{platform}：{health.status}，{health.detail}")
        raise typer.Exit(code=1)
    if platform != "rss":
        typer.echo(f"{platform} 已啟用，但第一階段單次測試只內建訂閱來源。")
        raise typer.Exit(code=1)
    feed = next(item for item in runtime.settings.bridge.get("feeds", []) if item.get("platform") == "rss")
    path = Path(feed["path"])
    if not path.is_absolute():
        path = runtime.settings.root / path
    probe = RssProbe(runtime.policy, probe_id="rss", source_type=str(spec.get("source_type", "web")))
    since = datetime.now(timezone.utc) - timedelta(days=14)
    try:
        items = asyncio.run(probe.fetch(str(path), since))
    except Exception as exc:  # noqa: BLE001 — 命令列要把探針失敗講成可讀結果
        runtime.record_probe(
            probe_id="rss",
            platform="rss",
            backend_id="feedparser",
            acquisition_mode=None,
            success=False,
            item_count=0,
            error=str(exc),
        )
        typer.echo(f"探針失敗：{exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"rss 取回 {len(items)} 條原始條目")
    for item in items:
        typer.echo(f"- {item.upstream_timestamp} {item.title} {item.source_url}")


@bridge_app.command("ingest")
def bridge_ingest(feed: str = typer.Option(..., "--feed", help="訂閱來源識別")) -> None:
    """手動觸發採集。"""
    runtime = _runtime()
    feeds = runtime.settings.bridge.get("feeds", [])
    spec = next((item for item in feeds if item.get("id") == feed), None)
    if spec is None:
        typer.echo(f"找不到訂閱來源：{feed}")
        raise typer.Exit(code=1)
    path = Path(spec["path"])
    if not path.is_absolute():
        path = runtime.settings.root / path
    probe = RssProbe(runtime.policy, probe_id=str(spec.get("probe_id", "rss")), source_type=str(spec.get("source_type", "web")))
    since = datetime.now(timezone.utc) - timedelta(days=14)
    try:
        items = asyncio.run(probe.fetch(str(path), since))
    except Exception as exc:  # noqa: BLE001
        runtime.record_probe(
            probe_id=probe.probe_id,
            platform="rss",
            backend_id="feedparser",
            acquisition_mode=None,
            success=False,
            item_count=0,
            error=str(exc),
        )
        typer.echo(f"採集失敗：{exc}")
        raise typer.Exit(code=1) from exc
    outcome = runtime.ingest_items(items)
    runtime.record_probe(
        probe_id=probe.probe_id,
        platform="rss",
        backend_id="feedparser",
        acquisition_mode="live",
        success=True,
        item_count=len(outcome.report.stored),
        error=None,
    )
    typer.echo(
        f"入庫 {len(outcome.report.stored)}，網址重複 {outcome.report.dropped_url}，"
        f"正文重複 {outcome.report.dropped_hash}，近似重複 {outcome.report.near_duplicates}，"
        f"拒絕 {len(outcome.report.rejected)}"
    )


@weave_event.command("list")
def event_list() -> None:
    """事件列表。"""
    runtime = _runtime()
    with session_scope(runtime.factory) as session:
        rows = session.execute(
            select(CanonicalEvent, EventAssessment)
            .join(EventAssessment, EventAssessment.event_id == CanonicalEvent.event_id, isouter=True)
            .order_by(CanonicalEvent.last_updated.desc())
        ).all()
        table = []
        for event, assessment in rows:
            table.append(
                [
                    event.event_id,
                    event.status,
                    "—" if assessment is None else f"{float(assessment.priority):.3f}",
                    "棄權" if assessment is not None and assessment.abstain else "納入",
                    event.title,
                ]
            )
    _print_table(["事件", "狀態", "優先級", "研判", "標題"], table)


@weave_event.command("show")
def event_show(event_id: str) -> None:
    """事件詳情與提及。"""
    runtime = _runtime()
    with session_scope(runtime.factory) as session:
        event = session.get(CanonicalEvent, event_id)
        if event is None:
            typer.echo("事件不存在")
            raise typer.Exit(code=1)
        assessment = session.get(EventAssessment, event_id)
        typer.echo(f"{event.title}")
        typer.echo(f"狀態 {event.status}　優先級 {float(assessment.priority) if assessment else 0:.3f}")
        if assessment:
            typer.echo(f"校準置信 {float(assessment.calibrated_confidence):.3f}　突發 {float(assessment.burst_score):.3f}")
            typer.echo(f"分量 {assessment.components}")
        mentions = session.scalars(select(EventMention).where(EventMention.event_id == event_id)).all()
        for mention in mentions:
            mark = "近似" if mention.duplicate_of else mention.acquisition_mode
            typer.echo(f"- [{mark}] {mention.upstream_timestamp} {mention.platform} {mention.source_url}")
            typer.echo(f"  {mention.title}")


@watch_app.command("alerts")
def watch_alerts() -> None:
    """告警佇列。"""
    runtime = _runtime()
    with session_scope(runtime.factory) as session:
        rows = session.scalars(select(AlertRow).order_by(AlertRow.created_at.desc())).all()
        table = []
        for row in rows:
            rules = ",".join(row.trigger_rules or [])
            table.append([row.alert_id, f"{float(row.priority):.3f}", rules, str(len(row.signal_urls or [])), row.summary])
    _print_table(["告警", "優先級", "觸發規則", "網址數", "摘要"], table)


@watch_app.command("score")
def watch_score(event_id: str) -> None:
    """優先級評分明細。"""
    runtime = _runtime()
    with session_scope(runtime.factory) as session:
        if session.get(CanonicalEvent, event_id) is None:
            typer.echo("事件不存在")
            raise typer.Exit(code=1)
        assessment = WatchService(session, runtime.settings).score_event(event_id)
        typer.echo(f"優先級 {float(assessment.priority):.4f}")
        typer.echo(f"校準置信 {float(assessment.calibrated_confidence):.4f}")
        typer.echo(f"突發 {float(assessment.burst_score):.4f}")
        typer.echo(f"棄權 {'是' if assessment.abstain else '否'}")
        for key, value in (assessment.components or {}).items():
            typer.echo(f"  {key}: {value}")


@herald_app.command("brief")
def herald_brief(period: str = typer.Option("day", "--period", help="day 或 week")) -> None:
    """生成簡報。"""
    runtime = _runtime()
    with session_scope(runtime.factory) as session:
        row = BriefService(session, runtime.settings).generate(period)
        typer.echo(row.body)
        typer.echo(f"簡報 {row.brief_id}")


@herald_app.command("review")
def herald_review() -> None:
    """列出待複核佇列，並指向工作台。"""
    runtime = _runtime()
    with session_scope(runtime.factory) as session:
        rows = session.scalars(select(MergeSuggestion).where(MergeSuggestion.status == "pending")).all()
        if not rows:
            typer.echo("目前沒有待處理的歸併建議。")
        for row in rows:
            typer.echo(
                f"- {row.suggestion_id} 分數 {float(row.score):.3f} {row.band} "
                f"{row.left_event_id} ← {row.right_event_id}"
            )
    typer.echo("複核工作台：http://127.0.0.1:5173/review")


@horn_app.command("test")
def horn_test() -> None:
    """告警通道乾跑測試。不向外部網址送出資料。"""
    runtime = _runtime()
    channels = list(runtime.settings.horn.get("channels", []))
    with session_scope(runtime.factory) as session:
        results = test_channels(session, channels)
    for result in results:
        mark = "通過" if result.ok else "失敗"
        typer.echo(f"{result.channel}：{mark}，{result.detail}")


@scene_app.command("list")
def scene_list() -> None:
    """列出場景。場景是配置，不新增流水線。"""
    runtime = _runtime()
    rows = []
    for scene in runtime.settings.scenes.values():
        rows.append([scene.scene_id, scene.title, scene.fusion, scene.time_sensitivity])
    _print_table(["場景", "名稱", "融合", "時間敏感度"], rows)


@scene_app.command("decide")
def scene_decide(sku: str, node: str = "onsale", path: str = "fast", scene: str = "double11") -> None:
    """對標準化 SKU 產出可複核結論。金額只來自規則引擎。"""
    runtime = _runtime()
    try:
        payload = CommerceService(runtime).decide(scene_id=scene, sku_code=sku, node=node, path=path)
    except CommerceError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(payload["label"])
    if payload.get("confidence_reduced"):
        typer.echo(f"歷史價格基線 {payload['baseline_days']} 日，未滿 {payload['baseline_required']} 日，結論置信度降低")
    for item in payload["ranking"]:
        typer.echo(f"{item['platform']}  實付 {item['payable']}  分數 {item['score']}")
    for line in payload["abstain"]["confirmations"]:
        typer.echo(f"待確認：{line}")
    for line in payload["abstain"]["risks"]:
        typer.echo(f"風險：{line}")


@demo_app.command("seed")
def demo_seed() -> None:
    """寫入虛構公開訊號，供第一階段驗收。"""
    result = seed()
    typer.echo(
        f"事件 {result.get('events', 0)}，入庫 {result.get('stored', 0)}，"
        f"近似 {result.get('near_duplicates', 0)}，已種入 {result.get('seeded', 0)}，"
        f"場景種入 {result.get('commerce_seeded', 0)}"
    )


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """啟動介面服務。"""
    import uvicorn

    uvicorn.run("prism.api.app:create_app", factory=True, host=host, port=port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
