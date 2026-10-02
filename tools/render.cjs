#!/usr/bin/env node
/* Render index.html with Playwright.
 *   node tools/render.cjs stills  <outDir> [t1 t2 …]   one PNG per time (default: one per beat)
 *   node tools/render.cjs video   <out.mp4>             60 fps, 4 subframes/frame blended with tmix
 *   node tools/render.cjs sfx     <out.json>            the scene's sound cue list
 * Needs NODE_PATH="$(npm root -g)" for the global playwright install. */
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const URL = 'file://' + path.join(ROOT, 'index.html') + '?render';
const FPS = 60, SUB = 4, WORKERS = +(process.env.WORKERS || 4);

async function openPage(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1440 }, deviceScaleFactor: 1 });
  page.on('console', m => { if (m.type() === 'warning' || m.type() === 'error') console.error('[page]', m.text()); });
  page.on('pageerror', e => { console.error('[pageerror]', e); process.exit(1); });
  await page.goto(URL);
  await page.evaluate(() => window.READY);
  return page;
}
const shot = async (page, t) => { await page.evaluate(t => window.seek(t), t); return page.screenshot({ type: 'png' }); };

async function stills(out, times) {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const page = await openPage(browser);
  const loop = await page.evaluate(() => window.LOOP);
  if (!times.length) times = Array.from({ length: Math.round(loop / 0.5) }, (_, i) => i * 0.5);
  for (const t of times) {
    const f = path.join(out, `t${t.toFixed(3).padStart(7, '0')}.png`);
    fs.writeFileSync(f, await shot(page, t));
  }
  await browser.close();
  console.log(`${times.length} stills → ${out}`);
}

function encoder(file) {
  // 4 subframes at 240/s → mean of each group of 4 → 60 fps
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS * SUB), '-c:v', 'png', '-i', '-',
    '-vf', `tmix=frames=${SUB}:weights=${'1 '.repeat(SUB).trim()},select='eq(mod(n\\,${SUB})\\,${SUB - 1})',setpts=N/${FPS}/TB`,
    '-r', String(FPS), '-c:v', 'libx264', '-preset', 'slow', '-crf', '12', '-pix_fmt', 'yuv420p', '-tune', 'animation', file],
    { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((res, rej) => ff.on('close', c => c ? rej(new Error('ffmpeg ' + c)) : res()));
  return { ff, done };
}
const write = (s, buf) => new Promise(r => s.write(buf) ? r() : s.once('drain', r));

async function video(out) {
  const browser = await chromium.launch();
  const probe = await openPage(browser);
  const loop = await probe.evaluate(() => window.LOOP);
  await probe.close();
  const frames = Math.round(loop * FPS), per = Math.ceil(frames / WORKERS);
  const tmp = fs.mkdtempSync(path.join(require('os').tmpdir(), 'seg-'));
  const t0 = Date.now();
  let doneFrames = 0;
  const segs = await Promise.all(Array.from({ length: WORKERS }, async (_, w) => {
    const a = w * per, b = Math.min(frames, a + per), file = path.join(tmp, `seg${w}.mp4`);
    const page = await openPage(browser);
    const { ff, done } = encoder(file);
    for (let f = a; f < b; f++) {
      for (let s = 0; s < SUB; s++) {
        // subframes centred on the frame time (open shutter), so frame `frames` ≡ frame 0
        const t = f / FPS + (s - (SUB - 1) / 2) / (FPS * SUB);
        await write(ff.stdin, await shot(page, t));
      }
      if (++doneFrames % 60 === 0) process.stdout.write(`\r${doneFrames}/${frames} frames  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
    }
    ff.stdin.end(); await done; await page.close();
    return file;
  }));
  await browser.close();
  const list = path.join(tmp, 'list.txt');
  fs.writeFileSync(list, segs.map(s => `file '${s}'`).join('\n'));
  await new Promise((res, rej) => spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list, '-c', 'copy', out], { stdio: 'inherit' })
    .on('close', c => c ? rej(new Error('concat ' + c)) : res()));
  console.log(`\n${frames} frames → ${out}`);
}

async function sfx(out) {
  const browser = await chromium.launch();
  const page = await openPage(browser);
  const data = await page.evaluate(() => ({ loop: window.LOOP, bpm: window.BPM, cues: window.SFX }));
  fs.writeFileSync(out, JSON.stringify(data, null, 1));
  await browser.close();
  console.log(`${data.cues.length} cues → ${out}`);
}

const [mode, out, ...rest] = process.argv.slice(2);
({ stills: () => stills(out, rest.map(Number)), video: () => video(out), sfx: () => sfx(out) })[mode]()
  .catch(e => { console.error(e); process.exit(1); });
