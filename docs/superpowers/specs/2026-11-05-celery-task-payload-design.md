# Celery 统一 payload + 公共包抽取设计

- 日期：2026-11-05
- 状态：已确认并实现中

本设计合并两件事：
1. Celery `.delay` 统一为单一 `payload` dict（与 `task_req` 同源）
2. 抽取跨服务公共库 `common/cmwk_common`（兑现迁移文档搁置的「方案 A」）

## 目标

- 所有 uscm 派发任务：`xxx.delay(payload=<dict>)`
- `payload` ≡ `task_req`（同一 dict → `json.dumps`）
- 公共字段：`task_id` / `uid` / `email` / `domain`
- 公共包经构建前 rsync 注入各服务 `build_assets/cmwk_common/`
- **无 shim / 无旧参双轨**：import 与任务签名一次全改

## 非目标

- 不改队列名、任务名、broker
- 不改 Docker build context（仍在服务目录 `docker build .`）
- 首期不抽 `file_delivery` / socat / quic（Phase B）

## 公共包

路径：`common/cmwk_common/`

模块：`task_payload` / `util`（仅 `gen_res_dict`）/ `module_enum` / `log` / `internal_auth` / `userserver_worker` / `userserver_producer`

注入：`common/sync_cmwk_common.sh <service_dir>`，各 `build-*-image.sh` 在 `docker build` 前调用。`**/build_assets/cmwk_common/` gitignore。

## Payload schema

信封：`task_id, uid, email, domain`

- indextts：+ modelname, text, modelcat, output_format, modellang, emo_type, emo_alpha, speed, volume_db
- tts：+ modelname, text, modelcat, output_format, modellang
- sound：+ text, length, num
- audio_extract / vocal / instrument：+ upfileinfo
- cover：+ modelcat, modelname, modelgender, upfileinfo, upgender, lang
- transcribe_whisper：+ upfileinfo, language, output_format
- htmlvideo：+ content, uri, source, aspect, style, duration, modelcat, modelname

不进 payload：`first_ip` / `real_ip` / `referer` / `tstamp` / `snature` / `sign_text`

## 发布

同批：rsync → build → 耗尽/清空队列 → 起新 worker → 发 uscm。
