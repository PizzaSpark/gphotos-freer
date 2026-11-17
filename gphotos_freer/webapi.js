// Helpers injected into a photos.google.com page. They call the same internal endpoints the
// Google Photos website uses (as documented by Google Photos Toolkit, github.com/xob0t).
// These are undocumented and may change.
(() => {
  const W = window.WIZ_global_data;
  const Q = [null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null,
             null, null, null, null, null, null, null, null, [], null, null, null, null, null, null, null,
             null, null, null, []];

  async function rpc(rpcid, data) {
    const body = `f.req=${encodeURIComponent(JSON.stringify([[[rpcid, JSON.stringify(data), null, 'generic']]]))}`
               + `&at=${encodeURIComponent(W.SNlM0e)}&`;
    const p = new URLSearchParams({ rpcids: rpcid, 'source-path': location.pathname, 'f.sid': W.FdrFJe,
                                    bl: W.cfb2h, pageId: 'none', rt: 'c' });
    for (let attempt = 1; attempt <= 4; attempt++) {
      try {
        const r = await fetch(`https://photos.google.com${W.eptZe}data/batchexecute?${p}`, {
          method: 'POST', credentials: 'include', body,
          headers: { 'content-type': 'application/x-www-form-urlencoded;charset=UTF-8' } });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const line = (await r.text()).split('\n').find(l => l.includes('wrb.fr'));
        if (!line) throw new Error('no wrb.fr');
        const payload = JSON.parse(line)[0][2];
        return payload ? JSON.parse(payload) : null;
      } catch (e) {
        if (attempt === 4) throw e;
        await new Promise(r => setTimeout(r, 1500 * attempt));
      }
    }
  }

  // Google's own per-item storage info for a list of media keys.
  async function spaceInfo(mediaKeys) {
    const out = {};
    for (let s = 0; s < mediaKeys.length; s += 250) {
      const keys = mediaKeys.slice(s, s + 250).map(k => [k]);
      const info = await rpc('EWgK9e', [[[keys], [Q]]]);
      for (const d of (info?.[0]?.[1] || [])) {
        const ext = d[1]?.at(-1);
        out[d[0]] = { name: d[1]?.[3], size: d[1]?.[9], uploaded: d[1]?.[8],
                      takesUpSpace: ext?.[0] === 1, spaceTaken: ext?.[1], original: ext?.[2] === 2 };
      }
    }
    return out;
  }

  window.gpf = {
    rpc,
    spaceInfo,

    // Whole library: every item that takes up space.
    async scan(progress) {
      const found = [];
      let pageId = null, pages = 0, items = 0;
      do {
        const page = await rpc('lcxiM', [pageId, null, 500, null, 1, 3]);
        const list = page?.[0] || [];
        pageId = page?.[1];
        pages++; items += list.length;
        const info = await spaceInfo(list.map(i => i[0]));
        for (const it of list) {
          const s = info[it[0]];
          if (s && s.takesUpSpace) {
            found.push({ mediaKey: it[0], dedupKey: it[3], taken: it[2], tzOffset: it[4],
                         owned: (it[7] || []).filter(a => a.includes(27)).length === 0, ...s });
          }
        }
        window.__gpfProgress = { pages, items, found: found.length };
      } while (pageId);
      return { items, found };
    },

    // Items uploaded since `sinceMs` (newest first), with Google's storage info.
    async recentUploads(sinceMs) {
      const keys = [];
      let pageId = null;
      do {
        const page = await rpc('EzkLib', ['', [[4, 'ra', 0, 0]], pageId]);
        const list = page?.[0] || [];
        pageId = page?.[1];
        let older = false;
        for (const it of list) {
          if (it[5] >= sinceMs) keys.push(it[0]); else older = true;
        }
        if (older) break;
      } while (pageId);
      const info = await spaceInfo(keys);
      return keys.map(k => ({ mediaKey: k, ...info[k] }));
    },

    // Download URL for the original file of one item.
    async originalUrl(mediaKey) {
      const info = await rpc('VrseUb', [mediaKey, null, null, null, null]);
      return info?.[7] || info?.[1] || null;
    },

    // Download URLs for many items, `parallel` requests at a time.
    async originalUrls(mediaKeys, parallel = 20) {
      const out = {};
      for (let s = 0; s < mediaKeys.length; s += parallel) {
        const chunk = mediaKeys.slice(s, s + parallel);
        const urls = await Promise.all(chunk.map(async mk => {
          try { const i = await rpc('VrseUb', [mk, null, null, null, null]); return i?.[7] || i?.[1] || null; }
          catch (e) { return null; }
        }));
        chunk.forEach((mk, i) => out[mk] = urls[i]);
      }
      return out;
    },

    // The website's "Download" button: Google builds a zip of the originals, then gives a URL.
    // Used for items (videos) where the single-file link isn't available.
    async zipUrl(mediaKeys, timeoutMs = 600000) {
      const tok = await rpc('yCLA7', [mediaKeys.map(k => [k])]);
      const token = tok?.[0];
      if (!token) return null;
      const end = Date.now() + timeoutMs;
      while (Date.now() < end) {
        const r = await rpc('dnv2s', [[token]]);
        const d = r?.[0]?.[0]?.[0]?.[2]?.[0];
        if (d?.[1]) return { fileName: d[0], url: d[1], size: d[2] };
        await new Promise(res => setTimeout(res, 3000));
      }
      return null;
    },

    // Move items to Trash by dedup key (reversible from Trash for 60 days).
    async trash(dedupKeys) {
      const done = [];
      for (let s = 0; s < dedupKeys.length; s += 100) {
        const chunk = dedupKeys.slice(s, s + 100);
        await rpc('XwAOJf', [null, 1, chunk, 3]);
        done.push(...chunk);
      }
      return done.length;
    },
  };
  return 'gpf ready';
})();
