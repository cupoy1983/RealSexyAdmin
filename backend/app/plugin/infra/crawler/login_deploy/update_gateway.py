"""Upgrade only the guarded viewer route; preserve existing API and Chinese UI config."""

import re
import sys
from pathlib import Path


def upgrade_page(text):
    if 'const collectorWindowHeaders=' in text:
        return text
    marker = 'function render(state){'
    refresh = "async function refresh(){try{const r=await fetch('/control/status',{cache:'no-store'});"
    if marker not in text or refresh not in text:
        raise ValueError('Login page layout is not recognized; manual upgrade required')
    helper = '''let collectorWindowFinished=false;
let collectorShownWindow=null;
let collectorStarting=false;
let collectorWindowId=sessionStorage.getItem('collector_window_id');
const collectorWindowHeaders=()=>({'X-Collector-Window':collectorWindowId||''});
const collectorViewer=document.querySelector('iframe');
const collectorViewerUrl=collectorViewer.getAttribute('src');
function collectorEndWindow(){
  collectorWindowFinished=true;collectorViewer.src='about:blank';
  for(const b of document.querySelectorAll('button'))b.disabled=true;
}
function collectorSyncViewer(state){
  const current=state.active?.window_id||null;
  if(current&&current!==collectorWindowId){
    if(!collectorStarting||collectorShownWindow){collectorEndWindow();return false}
    collectorWindowId=current;sessionStorage.setItem('collector_window_id',current);
  }
  if(current!==collectorShownWindow||!current){
    if(current){
      const url=new URL(collectorViewerUrl,location.origin);
      url.searchParams.set('path','viewer/websockify?window='+current);
      collectorViewer.src=url.pathname+url.search;
    }else collectorViewer.src='about:blank';
    if(collectorShownWindow&&!current)collectorEndWindow();
    collectorShownWindow=current;
  }
  return true;
}
'''
    text = text.replace(marker, helper + marker + '\n  if(!collectorSyncViewer(state))return;', 1)
    new_refresh = refresh.replace('try{', 'if(collectorWindowFinished||collectorStarting)return;const collectorRefreshWindow=collectorWindowId;try{').replace("cache:'no-store'", "cache:'no-store',headers:collectorWindowHeaders()")
    text = text.replace(refresh, new_refresh +
                        "if(collectorStarting||collectorRefreshWindow!==collectorWindowId)return;if(r.status===401){collectorEndWindow();message.textContent='窗口授权已结束，请从业务页面重新打开入口';return}", 1)
    text = text.replace("  try{const r=await fetch('/control/command'", "  collectorStarting=button.dataset.action==='start';\n  try{const r=await fetch('/control/command'", 1)
    text = text.replace("headers:{'Content-Type':'application/json'}", "headers:{'Content-Type':'application/json',...collectorWindowHeaders()}")
    return text.replace("finally{for(const b of document.querySelectorAll('button'))b.disabled=false}",
                        "finally{collectorStarting=false;for(const b of document.querySelectorAll('button'))b.disabled=collectorWindowFinished}")


def upgrade(text):
    pattern = r'(location\s*=\s*/viewer/websockify\s*\{)(.*?)(\n\s*\})'
    match = re.search(pattern, text, flags=re.S)
    if not match:
        raise ValueError('Gateway viewer route is not recognized; manual upgrade required')
    body = match[2]
    old = 'proxy_pass http://127.0.0.1:6080/websockify;'
    new = 'proxy_pass http://127.0.0.1:6081/vnc;'
    if old not in body and new not in body:
        raise ValueError('Gateway viewer upstream is not recognized; manual upgrade required')
    viewer = re.search(r'location\s+/viewer/\s*\{([^}]+)\}', text)
    if not viewer or not all(line in viewer[1] for line in ('proxy_set_header Upgrade "";', 'proxy_set_header Connection "";')):
        raise ValueError('Gateway generic viewer must disable WebSocket upgrades')
    body = body.replace(old, new)
    if not re.search(r'proxy_set_header\s+Host\s+', body):
        body += '\n            proxy_set_header Host $http_host;'
    if not re.search(r'proxy_set_header\s+X-Forwarded-Proto\s+', body):
        scheme = '$login_scheme' if ' $login_scheme {' in text else '$scheme'
        body += '\n            proxy_set_header X-Forwarded-Proto ' + scheme + ';'
    text = text[:match.start()] + match[1] + body + match[3] + text[match.end():]
    clipboard = re.search(r'(location\s+/text/\s*\{)([^}]+)(\})', text)
    if clipboard:
        body = clipboard[2]
        old = 'proxy_pass http://127.0.0.1:6082/;'
        new = 'proxy_pass http://127.0.0.1:6081/;'
        if old not in body and new not in body:
            raise ValueError('Clipboard upstream is not recognized; manual upgrade required')
        body = body.replace(old, new)
        if not re.search(r'proxy_set_header\s+X-Forwarded-Proto\s+', body):
            scheme = '$login_scheme' if ' $login_scheme {' in text else '$scheme'
            body += ' proxy_set_header X-Forwarded-Proto ' + scheme + '; '
        text = text[:clipboard.start()] + clipboard[1] + body + clipboard[3] + text[clipboard.end():]
    return text


if __name__ == '__main__':
    path = Path(sys.argv[1])
    updated = upgrade(path.read_text(encoding='utf-8'))
    page = path.parent / 'web' / 'index.html'
    # Validate both before changing either; preserve Chinese input scripts and styles.
    page_text = upgrade_page(page.read_text(encoding='utf-8'))
    path.write_text(updated, encoding='utf-8')
    page.write_text(page_text, encoding='utf-8')
