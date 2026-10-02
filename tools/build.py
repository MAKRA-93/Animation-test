#!/usr/bin/env python3
"""Inline Geist into src/index.src.html → index.html (one self-contained file)."""
import base64, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
src = (root / 'src/index.src.html').read_text()
font = base64.b64encode((root / 'assets/fonts/Geist-Variable.woff2').read_bytes()).decode()
(root / 'index.html').write_text(src.replace('/*GEIST*/', font))
print('index.html', len(src) // 1024, 'KB source +', len(font) // 1024, 'KB font')
