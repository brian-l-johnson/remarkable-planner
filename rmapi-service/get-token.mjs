#!/usr/bin/env node
/**
 * get-token.mjs
 *
 * One-time script to register with the reMarkable cloud and obtain
 * an rmapi-js token. Run this once, then seal the output as a
 * Kubernetes SealedSecret (see README.md).
 *
 * Usage:
 *   1. Go to https://my.remarkable.com/device/browser/connect
 *   2. Copy the eight-letter one-time code
 *   3. Run: node get-token.mjs XXXXXXXX
 *   4. Save the printed token — it goes into the k8s secret
 */

import { register } from "rmapi-js";

const code = process.argv[2];

if (!code || code.length !== 8) {
  console.error("Usage: node get-token.mjs <eight-letter-code>");
  console.error("Get your code at: https://my.remarkable.com/device/browser/connect");
  process.exit(1);
}

console.log(`Registering with code: ${code}...`);

try {
  const token = await register(code);
  console.log("\n✅ Registration successful. Your token:\n");
  console.log(token);
  console.log("\nSave this token — use it to create your SealedSecret (see README.md).");
} catch (err) {
  console.error("❌ Registration failed:", err.message);
  console.error("Make sure the code is correct and hasn't expired (they're valid for ~5 minutes).");
  process.exit(1);
}
