#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill-timing.py — Claude Code 转录中各 skill/阶段耗时统计

扫描 /app/log/claude/projects/**/*.jsonl,识别 skill 运行(三种签名):
  1. sdk-cli 流水线:首个用户消息里的 <command-name>/aiflow-build-storyboard</command-name>
  2. 交互式 Skill 工具调用:tool_use name==Skill 的 input.skill
  3. 子代理会话:<parent-session>/subagents/agent-*.jsonl(+ .meta.json 取 agentType)

统计口径(与 /app/log/docs/*-analysis.md 各阶段分析对齐):
  wall    = 会话首尾时间戳差(子代理等待计入父会话 wall,父会话行即阶段总耗时)
  ttfb    = 入队(enqueue)/首个用户消息 → 首个 assistant 消息
  rounds  = 含工具的 assistant 回合数(tool_turns;list/group 表口径,与各分析文档的"回合预算"对齐)
  tools   = tool_use 块总数;agents = Agent/Task/spawn 工具调用次数
  detail 额外给 assistant 消息总数(含纯 thinking 轮)与回合墙钟(工具回合→下一工具回合)
  tool_exec = 按 tool_use_id 配对 tool_result 时间戳的 Σ(结果-调用)
  model   = wall - tool_exec(思考+流式+每轮摊薄开销)
  tokens  = Σ message.usage(input/output/cache_read/cache_creation)

用法:
  python3 skill-timing.py                  # 全量 list 表格(按时间排序)
  python3 skill-timing.py --group          # 按 skill 聚合:runs/avg/min/max 耗时/回合/令牌
  python3 skill-timing.py <session-id|路径前缀>      # 单会话 detail 报告
  python3 skill-timing.py <id> --timeline            # detail + 工具时间线
  python3 skill-timing.py --since '2026-11-11' --agents --csv --top N
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT_DEFAULT = "/app/log/claude/projects"
SPAWN_TOOLS = {"Agent", "Task", "spawn"}


def parse_ts(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def iter_jsonl(path):
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------- identity


def detect_identity(path):
    """(kind, label) — kind ∈ {skill, agent, none}."""
    if "/subagents/agent-" in path or os.path.basename(path).startswith("agent-"):
        meta_path = path + ".meta.json"
        label = "agent"
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    m = json.load(f)
                at = (m.get("agentType") or m.get("name") or "agent").strip()
                label = f"agent:{at}"
                desc = (m.get("description") or "").strip()
                if desc:
                    label += " | " + desc[:40]
            except Exception:
                pass
        return ("agent", label)

    cmd_re = re.compile(r"<command-name>(?:/)?([a-zA-Z0-9_.-]+)</command-name>")
    cmd_skills = set()
    tool_skills = set()
    for d in iter_jsonl(path):
        msg = d.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if msg.get("role") == "user":
            texts = []
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        texts.append(b.get("text", ""))
            for tx in texts:
                for m in cmd_re.finditer(tx):
                    cmd_skills.add(m.group(1))
            if cmd_skills:  # 首个用户消息即命令,不再翻后面
                break
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                    s = (b.get("input") or {}).get("skill")
                    if s:
                        tool_skills.add("Skill:" + s)
    if not cmd_skills and not tool_skills:
        return ("none", "-")
    prim = sorted(cmd_skills)[0] if cmd_skills else sorted(tool_skills)[0]
    # 展示/聚合层面去掉 Skill: 前缀(两种签名归并为一个 skill 名)
    prim = prim.removeprefix("Skill:")
    return ("skill", prim)


# ---------------------------------------------------------------- metrics


def analyze(path):
    identity = detect_identity(path)

    t_first = t_last = None
    enqueue_ts = first_user_ts = first_asst_ts = None

    n_asst = 0
    asst_ts = []  # 所有 assistant 消息时间戳(回合墙钟用)
    turns = []  # (ts, [tool names]) 含工具的回合
    n_tools = 0
    n_agents = 0
    use_by_id = {}  # tool_use_id -> (ts, name)
    result_by_id = {}  # tool_use_id -> ts
    tool_hist = {}  # name -> [count, exec_s]

    toks = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    usage_msgs = 0
    think_msgs = think_chars = 0
    text_chars = 0

    for d in iter_jsonl(path):
        t = parse_ts(d.get("timestamp"))
        if t is None:
            continue
        if t_first is None:
            t_first = t
        t_last = t

        if d.get("type") == "queue-operation" and d.get("operation") == "enqueue":
            enqueue_ts = enqueue_ts or t

        msg = d.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        usage = msg.get("usage")
        if usage:
            usage_msgs += 1
            toks["input"] += usage.get("input_tokens", 0) or 0
            toks["output"] += usage.get("output_tokens", 0) or 0
            toks["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
            toks["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0

        if role == "assistant" and isinstance(content, list):
            blocks = [b for b in content if isinstance(b, dict)]
            real = [b for b in blocks if b.get("type") in ("text", "tool_use", "thinking")]
            if not real:
                continue
            n_asst += 1
            asst_ts.append(t)
            if first_asst_ts is None:
                first_asst_ts = t
            names = []
            for b in blocks:
                bt = b.get("type")
                if bt == "tool_use":
                    n_tools += 1
                    nm = b.get("name", "?")
                    names.append(nm)
                    if nm in SPAWN_TOOLS:
                        n_agents += 1
                    hid = b.get("id")
                    if hid:
                        use_by_id[hid] = (t, nm)
                elif bt == "thinking":
                    think_msgs += 1
                    think_chars += len(str(b.get("thinking", "")))
                elif bt == "text":
                    text_chars += len(str(b.get("text", "")))
            if names:
                turns.append((t, names))

        if role == "user":
            if isinstance(content, list) and first_user_ts is None:
                if any(isinstance(b, dict) and b.get("type") in ("text", "tool_result") for b in content):
                    first_user_ts = t
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        uid = b.get("tool_use_id")
                        if uid:
                            result_by_id[uid] = t

    # 工具配对与直方图
    tool_exec = 0.0
    for hid, (ut, nm) in use_by_id.items():
        rt = result_by_id.get(hid)
        if rt is not None and rt >= ut:
            dt = (rt - ut).total_seconds()
            tool_exec += dt
            tool_hist.setdefault(nm, [0, 0.0])
            tool_hist[nm][0] += 1
            tool_hist[nm][1] += dt
        else:
            tool_hist.setdefault(nm, [0, 0.0])
            tool_hist[nm][0] += 1

    # 回合墙钟:含工具回合 → 下一个含工具回合(最后一个取会话尾)
    turn_walls = []
    for idx, (tt, _) in enumerate(turns):
        nxt = turns[idx + 1][0] if idx + 1 < len(turns) else t_last
        turn_walls.append((nxt - tt).total_seconds())

    wall = (t_last - t_first).total_seconds() if (t_last and t_first) else 0
    t0 = enqueue_ts or first_user_ts or t_first
    ttfb = (first_asst_ts - t0).total_seconds() if (first_asst_ts and t0) else None
    if ttfb is not None and ttfb < 0:  # 续写/无入队的杂项会话,ttfb 无意义
        ttfb = None

    return {
        "path": path,
        "kind": identity[0],
        "label": identity[1],
        "start": t_first,
        "end": t_last,
        "t0": t0,
        "ttfb": ttfb,
        "wall": wall,
        "turns": n_asst,
        "tool_turns": len(turns),
        "tools": n_tools,
        "agents": n_agents,
        "tool_exec": tool_exec,
        "model": wall - tool_exec,
        "tokens": toks,
        "usage_msgs": usage_msgs,
        "think_msgs": think_msgs,
        "think_chars": think_chars,
        "text_chars": text_chars,
        "tool_hist": tool_hist,
        "turns_data": turns,
        "turn_walls": turn_walls,
    }


# ---------------------------------------------------------------- formatting


def fmt_dur(s, wide=False):
    if s is None:
        return "-"
    if s >= 3600:
        h = s / 3600
        return f"{h:.2f}h" if wide else f"{h:.1f}h"
    if s >= 60:
        m = s / 60
        return f"{m:.1f}m"
    return f"{s:.1f}s"


def rel(p, root):
    try:
        return os.path.relpath(p, root)
    except ValueError:
        return p


def short_path(relp, full=False, w=34):
    if full or len(relp) <= w:
        return relp
    head, tail = relp[:10], relp[-w + 11:]
    return f"{head}…{tail}"


def fmt_count(n):
    return f"{n:,}"


# ---------------------------------------------------------------- discovery


def discover(root, since=None, include_agents=False):
    files = []
    for p in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        if p.endswith(".meta.json"):
            continue
        if not include_agents and "/subagents/" in p:
            continue
        if os.path.basename(p).startswith("agent-") and not include_agents:
            continue
        files.append(p)
    files.sort(key=lambda p: p.replace("\\", "/").lower())

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            try:
                since_dt = datetime.strptime(since, "%Y-%m-%d %H:%M")
            except ValueError:
                since_dt = datetime.strptime(since, "%Y-%m-%d")
        if since_dt and since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)

    out = []
    for p in files:
        st = analyze(p)
        if st["start"] is None:
            continue
        if since_dt and st["start"] < since_dt:
            continue
        out.append(st)
    out.sort(key=lambda x: x["start"])
    return out


# ---------------------------------------------------------------- reports

LIST_HDR = (
    "time (utc)      │ path                                   │ skill                      │"
    " ttfb   │ dur     │ ag  │ rounds │ tools │ model   │ in      │ out      │ cread"
)


def list_report(stats, full=False, csv=False):
    if not stats:
        print("no sessions found"); return
    if csv:
        print("time,path,skill,ttfb_s,dur_s,agents,rounds,tools,model_s,in,out,cache_read,wall_s")
        for s in stats:
            t = s["start"].strftime("%Y-%m-%d %H:%M:%S") if s["start"] else ""
            print(
                f'{t},{rel(s["path"], root)},{s["label"]},{s["ttfb"] or ""},{s["wall"]:.0f},'
                f'{s["agents"]},{s["tool_turns"]},{s["tools"]},{s["model"]:.0f},'
                f'{s["tokens"]["input"]},{s["tokens"]["output"]},{s["tokens"]["cache_read"]}'
            )
        return
    print(LIST_HDR)
    for s in stats:
        t = s["start"].strftime("%m-%d %H:%M") if s["start"] else "-"
        print(
            f'{t:<19}│ {short_path(rel(s["path"], root), full):<38}│ {s["label"][:27]:<27}│ '
            f'{fmt_dur(s["ttfb"]):<7}│ {fmt_dur(s["wall"]):<7}│ {s["agents"]:<4}│ '
            f'{s["tool_turns"]:<7}│ {s["tools"]:<6}│ {fmt_dur(s["model"]):<7}│ '
            f'{fmt_count(s["tokens"]["input"]):<8}│ {fmt_count(s["tokens"]["output"]):<8}│ {fmt_count(s["tokens"]["cache_read"]):<8}'
        )
    print(f"\n{len(stats)} sessions, total wall {fmt_dur(sum(s['wall'] for s in stats))}")


def group_report(stats):
    if not stats:
        print("no sessions found"); return
    groups = {}
    for s in stats:
        g = groups.setdefault(s["label"], [])
        g.append(s)
    rows = []
    for label, gs in groups.items():
        walls = sorted(s["wall"] for s in gs)
        n = len(walls)
        med = (walls[n // 2] + walls[n // 2 - 1]) / 2 if n % 2 == 0 else walls[n // 2]
        rows.append({
            "label": label,
            "n": len(gs),
            "avg": sum(walls) / len(walls),
            "med": med,
            "min": walls[0],
            "max": walls[-1],
            "rounds": sum(s["tool_turns"] for s in gs) // len(gs),
            "out": sum(s["tokens"]["output"] for s in gs),
            "cread": sum(s["tokens"]["cache_read"] for s in gs),
        })
    rows.sort(key=lambda r: -r["avg"])
    print(f"{'skill':<30}│ runs │ avg    │ med    │ min     │ max     │ rounds │ out      │ cread")
    for r in rows:
        print(
            f"{r['label'][:29]:<30}│ {r['n']:<4} │ {fmt_dur(r['avg']):<6} │ {fmt_dur(r['med']):<6} │ "
            f"{fmt_dur(r['min']):<7} │ {fmt_dur(r['max']):<7} │ {r['rounds']:<6} │ {fmt_count(r['out']):<8} │ {fmt_count(r['cread']):<8}"
        )


def detail_report(s, timeline=False):
    print(f"skill/time - {s['label']}")
    print(f"session     {rel(s['path'], root)}")
    print(f"start       {s['start']}  end {s['end']}")
    print("")
    wall = s["wall"]
    te = s["tool_exec"]
    md_ = s["model"]
    print(f"总账   wall {fmt_dur(wall, True)} = 工具 {fmt_dur(te, True)} ({te/wall*100:.0f}%) + 模型侧 {fmt_dur(md_, True)} ({md_/wall*100:.0f}%)")
    print(f"       ttfb {fmt_dur(s['ttfb'])};回合(assistant msg)= {s['turns']}(含工具 {s['tool_turns']});"
          f"工具调用 {s['tools']};子代理 {s['agents']}")
    tw = s["turn_walls"]
    if tw:
        tws = sorted(tw)
        print(f"       回合墙钟 median {tws[len(tws)//2]:.0f}s mean {sum(tw)/len(tw):.1f}s p90 {tws[min(len(tws)-1,int(len(tw)*0.9))]:.0f}s max {tws[-1]:.0f}s")
    T = s["tokens"]
    print(f"令牌   in {fmt_count(T['input'])} out {fmt_count(T['output'])} "
          f"cache_read {fmt_count(T['cache_read'])} cache_write {fmt_count(T['cache_creation'])} (usage msgs {s['usage_msgs']})")
    print(f"思考   {s['think_msgs']} blocks / {fmt_count(s['think_chars'])} chars;助手文本 {fmt_count(s['text_chars'])} chars")

    print("\n-- 耗时最长的 8 个回合 --")
    top = sorted(zip(s["turns_data"], tw), key=lambda x: -x[1])[:8]
    for (tt, names), w in top:
        print(f"  {tt.strftime('%H:%M:%S')} +{w:6.1f}s  {','.join(names)[:60]}")

    print("\n-- 工具统计(name: 次数 / 总执行) --")
    for nm, (cnt, exc) in sorted(s["tool_hist"].items(), key=lambda kv: -kv[1][1]):
        print(f"  {nm:<28} {cnt:>4}  {fmt_dur(exc):>8}")

    if timeline:
        print("\n-- 工具时间线 --")
        prev = None
        for (tt, names) in s["turns_data"]:
            gap = (tt - prev).total_seconds() if prev else 0
            print(f"  {tt.strftime('%H:%M:%S')} +{gap:6.1f}s  {','.join(names)[:70]}")
            prev = tt


# ---------------------------------------------------------------- cli


def main():
    global root
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default=None, help="session-id 前缀 / jsonl 路径 / all")
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--group", action="store_true", help="按 skill 聚合")
    ap.add_argument("--since", default=None, help="仅统计该时间之后的运行(YYYY-MM-DD[ HH:MM])")
    ap.add_argument("--agents", action="store_true", help="列入子代理会话")
    ap.add_argument("--all", action="store_true", help="列入所有会话(含非 skill 会话)")
    ap.add_argument("--timeline", action="store_true", help="detail 附工具时间线")
    ap.add_argument("--csv", action="store_true", help="list 输出 CSV")
    ap.add_argument("--full", action="store_true", help="显示完整路径")
    args = ap.parse_args()
    root = args.root

    if args.target and args.target != "all":
        cand = args.target
        if not cand.endswith(".jsonl"):
            hits = []
            for p in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
                if cand in p:
                    hits.append(p)
            if len(hits) == 1:
                cand = hits[0]
            elif len(hits) > 1:
                # 精确 session-id 匹配优先
                exact = [p for p in hits if os.path.basename(p).split("-")[0] == cand or os.path.basename(p).startswith(cand)]
                if len(exact) == 1:
                    cand = exact[0]
                else:
                    print(f"ambiguous '{cand}':")
                    for p in hits[:20]:
                        print("  ", rel(p, root))
                    sys.exit(1)
            else:
                # 最后尝试按 session 文件名基础部分匹配(忽略子目录 agent-)
                print(f"no transcript matching '{cand}' under {root}")
                sys.exit(1)
        stats = [analyze(cand)]
        if stats[0]["start"] is None:
            print(f"{cand}: unreadable/empty"); sys.exit(1)
        detail_report(stats[0], timeline=args.timeline)
        return

    stats = discover(root, since=args.since, include_agents=args.agents)
    if not args.all:
        stats = [s for s in stats if s["kind"] == "skill" or (args.agents and s["kind"] == "agent")]
    if args.group:
        group_report(stats)
    else:
        list_report(stats, full=args.full, csv=args.csv)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(0)