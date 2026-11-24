"""离线页面快照与 URL 规整；目标站业务标识由调用方额外提取。"""

from urllib.parse import urlsplit, urlunsplit

SNAPSHOT_SCRIPT = r"""(() => {
  const safeUrl = value => { try { const u = new URL(value, location.href); return ['http:','https:'].includes(u.protocol) ? u.origin + u.pathname : ''; } catch { return ''; } };
  const copy = document.documentElement.cloneNode(true);
  copy.querySelectorAll('script,noscript,iframe,object,embed,base,input[type=hidden],meta,style,link').forEach(e=>e.remove());
  copy.querySelectorAll('*').forEach(e=>{
    const editable = e.matches('textarea,[contenteditable]');
    [...e.attributes].forEach(a=>{
      if (['href','src','action','formaction','poster','srcset'].includes(a.name)) {
        e.removeAttribute(a.name);
        if(a.name!=='srcset') e.setAttribute('data-snapshot-'+a.name,safeUrl(a.value));
      } else if (!/^(id|class|name|role|type|disabled|checked|selected|alt|title|lang|dir|hidden|colspan|rowspan|aria-[a-z-]+)$/.test(a.name)) e.removeAttribute(a.name);
    });
    if (e.matches('input')) { e.removeAttribute('value'); e.value=''; }
    if (editable) e.textContent='';
  });
  const policy = document.createElement('meta');
  policy.httpEquiv='Content-Security-Policy';
  policy.content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'";
  (copy.querySelector('head')||copy).prepend(policy);
  return {url:safeUrl(location.href),title:document.title,html:'<!doctype html>\n'+copy.outerHTML};
})()"""


def safe_url(value):
    try:
        parsed = urlsplit(value or "")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return None
