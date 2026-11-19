"""htmlvideo create 流水线阶段：计时、并行组合、异常转 StepResult。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from cmwk_common.module_enum import TaskStatus
from cmwk_common.userserver_worker import update_task_status

from html_utility.audio import caption_generate, sync_duration, tts_generate
from html_utility.frame_html import build_frame_html, build_frame_packets
from html_utility.init_htmlvideo import init_htmlvideo
from html_utility.render import render_video
from html_utility.storyboard import build_storyboard
from html_utility.verify import verify_frame
from html_utility.visual import build_frame_visual


@dataclass
class StepResult:
    ret: int
    msg: str = "ok"
    cost: float = 0.0
    path: str | None = None
    extra_costs: dict[str, float] = field(default_factory=dict)


@dataclass
class TtsVisualResult:
    tts: StepResult
    visual: StepResult


@dataclass
class CaptionFramesResult:
    caption: StepResult
    frames: StepResult


def step_init(
    logger: Any,
    project_dir: str,
    *,
    resolution,
    content: str,
    message: str,
    aspect,
    duration,
    language: str,
    audience: str,
    angle: str,
    summary: str,
    customization: str,
    notes: str,
    style_preset=None,
    task_id: str = "",
) -> StepResult:
    t0 = time.time()
    ret, msg = init_htmlvideo(
        logger,
        project_dir,
        resolution=resolution,
        content=content,
        message=message,
        aspect=aspect,
        duration=duration,
        language=language,
        audience=audience,
        angle=angle,
        summary=summary,
        customization=customization,
        notes=notes,
        style_preset=style_preset,
        task_id=task_id,
    )
    return StepResult(ret=ret, msg=msg, cost=time.time() - t0)


def step_storyboard(logger: Any, project_dir: str) -> StepResult:
    t0 = time.time()
    try:
        path = build_storyboard(project_dir, logger)
        return StepResult(ret=0, msg="ok", cost=time.time() - t0, path=path)
    except Exception as e:
        return StepResult(
            ret=1,
            msg=f"Failed to build storyboard: {e}",
            cost=time.time() - t0,
        )


def _report_progress(logger: Any, task_id: str, domain: str, status, info_msg: str) -> None:
    """过程态上报不阻塞并行：先打 info，再 daemon 线程更新状态。"""
    logger.info(info_msg)
    threading.Thread(
        target=update_task_status,
        args=(logger, task_id, domain, status),
        kwargs={"payload_msg": status},
        daemon=True,
        name=f"update-status-{task_id}-{status}",
    ).start()


def _run_tts(
    logger: Any,
    project_dir: str,
    modelcat: str,
    modelname: str,
    task_id: str,
) -> StepResult:
    t0 = time.time()
    ret, msg = tts_generate(logger, project_dir, modelcat, modelname, task_id)
    return StepResult(ret=ret, msg=msg, cost=time.time() - t0)


def _run_visual(logger: Any, project_dir: str) -> StepResult:
    t0 = time.time()
    try:
        path = build_frame_visual(project_dir, logger)
        return StepResult(ret=0, msg="ok", cost=time.time() - t0, path=path)
    except Exception as e:
        return StepResult(
            ret=1,
            msg=f"Failed to build frame visual: {e}",
            cost=time.time() - t0,
        )


def parallel_tts_visual(
    logger: Any,
    project_dir: str,
    modelcat: str,
    modelname: str,
    task_id: str,
    domain: str,
) -> TtsVisualResult:
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_tts = ex.submit(_run_tts, logger, project_dir, modelcat, modelname, task_id)
        fut_visual = ex.submit(_run_visual, logger, project_dir)
        pending = {fut_tts: "tts", fut_visual: "visual"}
        done: dict[str, StepResult] = {}
        for fut in as_completed(pending):
            kind = pending[fut]
            result = fut.result()
            done[kind] = result
            if result.ret != 0:
                continue
            if kind == "tts":
                _report_progress(
                    logger,
                    task_id,
                    domain,
                    TaskStatus.VIDEO_TTSED,
                    f"task_id:{task_id} tts generated, project_dir:{project_dir} "
                    f"cost:{result.cost:.1f}s",
                )
            else:
                _report_progress(
                    logger,
                    task_id,
                    domain,
                    TaskStatus.VIDEO_VISUALED,
                    f"task_id:{task_id} frame visual built, frame_visual_path:{result.path}, "
                    f"project_dir:{project_dir} cost:{result.cost:.1f}s",
                )
        return TtsVisualResult(tts=done["tts"], visual=done["visual"])


def step_sync_duration(logger: Any, project_dir: str, task_id: str) -> StepResult:
    t0 = time.time()
    ret, msg = sync_duration(logger, project_dir, task_id)
    return StepResult(ret=ret, msg=msg, cost=time.time() - t0)


def _run_caption(logger: Any, project_dir: str, task_id: str) -> StepResult:
    t0 = time.time()
    ret, msg = caption_generate(logger, project_dir, task_id)
    return StepResult(ret=ret, msg=msg, cost=time.time() - t0)


def _run_packets_and_html(
    logger: Any,
    project_dir: str,
    task_id: str,
    domain: str,
) -> StepResult:
    t0 = time.time()
    ret_pkt, msg_pkt = build_frame_packets(logger, project_dir, task_id)
    if ret_pkt != 0:
        cost_packets = time.time() - t0
        return StepResult(
            ret=ret_pkt,
            msg=msg_pkt,
            cost=cost_packets,
            extra_costs={"packets": cost_packets, "frame_html": 0.0},
        )
    cost_packets = time.time() - t0
    logger.info(
        f"task_id:{task_id} frame packets built, project_dir:{project_dir} "
        f"cost:{cost_packets:.1f}s"
    )
    update_task_status(
        logger,
        task_id,
        domain,
        TaskStatus.VIDEO_FRAME_PACKETED,
        payload_msg=TaskStatus.VIDEO_FRAME_PACKETED,
    )
    t1 = time.time()
    try:
        path = build_frame_html(project_dir, logger)
        cost_frame_html = time.time() - t1
        return StepResult(
            ret=0,
            msg="ok",
            cost=cost_packets + cost_frame_html,
            path=path,
            extra_costs={"packets": cost_packets, "frame_html": cost_frame_html},
        )
    except Exception as e:
        cost_frame_html = time.time() - t1
        return StepResult(
            ret=1,
            msg=f"Failed to build frame html: {e}",
            cost=cost_packets + cost_frame_html,
            extra_costs={"packets": cost_packets, "frame_html": cost_frame_html},
        )


def parallel_caption_frames(
    logger: Any,
    project_dir: str,
    task_id: str,
    domain: str,
) -> CaptionFramesResult:
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_caption = ex.submit(_run_caption, logger, project_dir, task_id)
        fut_frames = ex.submit(
            _run_packets_and_html, logger, project_dir, task_id, domain
        )
        pending = {fut_caption: "caption", fut_frames: "frames"}
        done: dict[str, StepResult] = {}
        for fut in as_completed(pending):
            kind = pending[fut]
            result = fut.result()
            done[kind] = result
            if result.ret != 0:
                continue
            if kind == "caption":
                _report_progress(
                    logger,
                    task_id,
                    domain,
                    TaskStatus.VIDEO_CAPTIONED,
                    f"task_id:{task_id} caption generated, project_dir:{project_dir} "
                    f"cost:{result.cost:.1f}s",
                )
            else:
                cost_frame_html = result.extra_costs.get("frame_html", 0.0)
                _report_progress(
                    logger,
                    task_id,
                    domain,
                    TaskStatus.VIDEO_FRAMED_HTML,
                    f"task_id:{task_id} frame html built, frame_html_path:{result.path}, "
                    f"project_dir:{project_dir} cost:{cost_frame_html:.1f}s",
                )
        return CaptionFramesResult(caption=done["caption"], frames=done["frames"])


def step_verify(logger: Any, project_dir: str) -> StepResult:
    t0 = time.time()
    try:
        path = verify_frame(project_dir, logger)
        return StepResult(ret=0, msg="ok", cost=time.time() - t0, path=path)
    except Exception as e:
        return StepResult(
            ret=1,
            msg=f"Failed to verify frame: {e}",
            cost=time.time() - t0,
        )


def step_render(
    logger: Any,
    project_dir: str,
    video_name: str,
    task_id: str,
) -> StepResult:
    t0 = time.time()
    try:
        ret, msg = render_video(logger, project_dir, video_name, task_id)
        return StepResult(ret=ret, msg=msg, cost=time.time() - t0)
    except Exception as e:
        return StepResult(
            ret=1,
            msg=f"Failed to render video: {e}",
            cost=time.time() - t0,
        )
