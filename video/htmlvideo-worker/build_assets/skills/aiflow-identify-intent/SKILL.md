---
name: aiflow-identify-intent
description: >-
  Extract video intent (message, angle, audience, title, summary, customization,
  notes) from a topic, article, notes, or arbitrary text; return JSON and save
  to the given output file path. Use when recognizing user intention for MP4 video
  creation, drafting intent.json, or analyzing source text before video generation.
argument-hint: [output-file]
arguments: [output_file]
---

# Recognize Intention

Extract structured video-creation intent from user-provided text, return JSON, and save it to `$output_file`.

`$output_file` is the **full path** of the output file (directory + filename), e.g. `/aiflow-identify-intent ./briefs/intent.json`. If `$output_file` is empty, default to `./intent.json`.

## When to use

- User provides a topic, article, notes, or brief that will become an MP4 video
- User asks to recognize / extract intention, draft `intent.json`, or prepare video brief fields
- Upstream of storyboard / script / HTML video generation

## Input

Users will provide a topic, article, notes, or arbitrary text. This input will ultimately be used to create an MP4 video.

Optionally, the caller passes the full output file path as the skill argument (`$output_file`), including directory and filename.

## Task

Your task is to extract the following information from the input, return it in JSON format, and save it to `$output_file` (default `./intent.json`):

A. message
The one key message the video must communicate. Derive and echo a single sentence that captures the core message of the input.

B. angle
The story structure or presentation format, such as concept, how-to, listicle, or narrative. Recommend the format that best matches the natural structure of the input.

C. audience
Who is the intended audience? Infer the target viewers from the input.

D. name
A suitable title for the content and the video to be generated. Keep it concise, with a maximum of 15 characters.

E. summary
A a short paragraph: what the video is about, who it is for, and why it matters now. Preserve the tone and feel in the user's own words.

F. customization (Optional)
Identify important details, key points, or unique-value elements explicitly mentioned in the input that could be incorporated into the subsequent video creation process.

G. notes (Optional)
Identify any constraints or requirements mentioned in the input, including content that should be handled carefully, emphasized, or avoided.

## Workflow

1. Resolve the output path: if `$output_file` is non-empty, use it as-is; otherwise use `./intent.json`. Create the parent directory if it does not exist.
2. Read the user's full input (do not summarize away details needed for fields F/G).
3. Fill required fields A–E; add F/G only when the input explicitly supports them.
4. Emit a single JSON object using the schema below.
5. Write that JSON (pretty-printed, UTF-8) to the resolved `$output_file` path.
6. Briefly confirm what was written (full path + `name` + one-line `message`).

## Output schema

```json
{
  "message": "string — one sentence, core message",
  "angle": "string — e.g. concept | how-to | listicle | narrative",
  "audience": "string — intended audience / target viewers",
  "name": "string — title, max 15 characters",
  "summary": "string — short paragraph; preserve user tone",
  "arc": "string | omit if none — story structure, e.g. \"how-to-process (Hook → Thesis → Steps → CTA)\"",
  "customization": "string | omit if none",
  "notes": "string | omit if none"
}
```

Rules:

- Always include `message`, `angle`, `audience`, `name`, `summary`.
- Omit `arc` / `customization` / `notes` when empty (do not write null or `""`).
- `name` must be ≤ 15 characters (count Unicode code points; CJK counts as 1 each).
- Do not wrap the file contents in markdown fences; the output file must be valid JSON only.
- Prefer the input language for all string values unless the user asks otherwise.
- `$output_file` is a full file path (directory + filename); do not append an extra filename.

## Example

Invocation: `/aiflow-identify-intent ./briefs/intent.json`

Input:

```text
Why compound interest can change an ordinary person's wealth trajectory: invest a little every month via dollar-cost averaging, and growth accelerates noticeably over a longer time horizon. Aimed at young beginners just starting to manage money; emphasize patience and persistence, and do not promise specific returns.
```

`./briefs/intent.json`:

```json
{
  "message": "Long-term dollar-cost averaging with compound interest can meaningfully change an ordinary person's wealth trajectory.",
  "angle": "concept",
  "audience": "Young beginners just starting to manage their money",
  "name": "How Compound Interest Changes Wealth Trajectories",
  "summary": "Use compound interest and dollar-cost averaging to show how ordinary people can widen the wealth gap over time. Aimed at personal-finance beginners, it stresses patience and consistency, without promising specific returns.",
  "customization": "Monthly DCA; growth accelerates as the time horizon lengthens",
  "notes": "Do not promise specific returns; emphasize patience and persistence"
}
```
