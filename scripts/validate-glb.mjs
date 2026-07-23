#!/usr/bin/env node
// Check a produced GLB against the Khronos glTF-Validator, the same tool the
// glTF ecosystem uses, so "loads in three.js" is a checked claim rather than an
// assertion.
//
//   node scripts/validate-glb.mjs out/asset.glb
//
// Kept out of scripts/test.sh on purpose: the layer test suites are stdlib
// only and run with no network, and this one needs a package. Run it when the
// exporter changes.
//
//   npm i -g gltf-validator      (or: npx --yes gltf-validator)

import { readFile } from 'node:fs/promises'
import { basename, join } from 'node:path'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'

const path = process.argv[2]
if (!path) {
  console.error('usage: node scripts/validate-glb.mjs <file.glb>')
  process.exit(2)
}

// Resolve from the current directory first, so `npm i gltf-validator` in any
// scratch folder works without installing anything into this repo.
let validator
for (const resolve of [
  () => createRequire(pathToFileURL(join(process.cwd(), 'noop.js')))('gltf-validator'),
  () => createRequire(import.meta.url)('gltf-validator'),
]) {
  try {
    validator = resolve()
    break
  } catch { /* try the next resolution root */ }
}
if (!validator) {
  console.error('gltf-validator not found. From any directory:\n' +
                '  npm i gltf-validator && node ' + process.argv[1] + ' ' + path)
  process.exit(2)
}

const bytes = new Uint8Array(await readFile(path))
const report = await validator.validateBytes(bytes, {
  uri: basename(path),
  externalResourceFunction: () =>
    Promise.reject(new Error('this GLB should be self-contained; it referenced an external resource')),
})

const { numErrors, numWarnings, numInfos } = report.issues
console.log(`${basename(path)}: ${numErrors} errors, ${numWarnings} warnings, ${numInfos} infos`)
console.log(`  generator:  ${report.info?.generator ?? 'unknown'}`)
console.log(`  version:    glTF ${report.info?.version}`)
console.log(`  extensions: ${(report.info?.extensionsUsed ?? []).join(', ') || 'none'}`)
console.log(`  primitives: ${report.info?.totalTriangleCount ?? '?'} triangles, ` +
            `${report.info?.totalVertexCount ?? '?'} vertices`)
console.log(`  textures:   ${(report.info?.resources ?? []).length}`)

for (const issue of report.issues.messages.slice(0, 20)) {
  console.log(`  [${issue.severity === 0 ? 'ERROR' : issue.severity === 1 ? 'WARN' : 'INFO'}] ` +
              `${issue.pointer || ''} ${issue.message}`)
}

process.exit(numErrors > 0 ? 1 : 0)
