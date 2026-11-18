// Expand Anthropic SDK debug logs past Bun/Node default truncation.
// Loaded via: BUN_OPTIONS=--preload=/abs/path/to/claude_inspect_full.js
// (Claude Code ships as a Bun-compiled binary; NODE_OPTIONS --require does not run.)
'use strict';

try {
  const util = require('util');
  util.inspect.defaultOptions.depth = null;
  util.inspect.defaultOptions.maxArrayLength = null;
  util.inspect.defaultOptions.maxStringLength = null;
  util.inspect.defaultOptions.breakLength = 120;
} catch (err) {
  // Never block Claude startup if the inspect patch fails.
  try {
    process.stderr.write(`[claude_inspect_full] patch failed: ${err}\n`);
  } catch (_) {}
}
