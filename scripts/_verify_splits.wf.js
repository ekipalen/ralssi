export const meta = {
  name: 'verify-noyt-splits',
  description: 'Verify no-ytunnus org split candidates as same-entity, precision-first',
  phases: [{ title: 'Classify' }, { title: 'Refute' }],
}

const dir = (typeof args === 'string') ? args : args.dir
const n = (typeof args === 'string') ? 286 : args.n
const pad = (i) => String(i).padStart(3, '0')

const CLASSIFY_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    from: { type: 'integer' }, to: { type: 'integer' },
    merge: { type: 'boolean' },
    confidence: { type: 'string', enum: ['high', 'med', 'low'] },
    failuremode: { type: 'string', enum: ['none', 'locality', 'chapter_vs_parent', 'coincidence', 'different_subtype', 'unsure'] },
    reason: { type: 'string' },
  },
  required: ['from', 'to', 'merge', 'confidence', 'failuremode', 'reason'],
}
const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: { refuted: { type: 'boolean' }, reason: { type: 'string' } },
  required: ['refuted', 'reason'],
}

function classifyPrompt(file) {
  return `Read the JSON file at ${file}. It has fields: from, to (org_ids), a (cluster A names), b (cluster B names), shared (shared distinctive tokens), kind.

Judge whether cluster A and cluster B are the SAME registered legal entity (same Y-tunnus / business ID), so their funding records should be merged.

PRECISION-FIRST: merging two genuinely different entities is far worse than leaving one entity split. Default merge=false unless confident.

merge=true ONLY if same registered org, e.g.:
- bilingual FI/SV/EN variants of one name (Tringa ry FI vs SV)
- acronym/abbreviation vs full name (METKA = Metropolia opiskelijakunta; GTK = Geological Survey of Finland)
- casing/punctuation/whitespace differences only

merge=false (pick failuremode):
- locality: same brand, different city/region (Turun AMK vs Lapin AMK; Helsinki vs Espoo)
- chapter_vs_parent: national liitto/keskus vs local osasto/piiri/paikallisyhdistys, or umbrella vs member
- coincidence: share a generic word but unrelated
- different_subtype: related but distinct registered orgs (Syöpäpotilaat vs Gynekologiset Syöpäpotilaat)
- unsure: not enough evidence

Return from and to from the file. Reason in Finnish, one sentence.`
}
function refutePrompt(file, lens) {
  const t = lens === 'locality'
    ? 'Focus on PLACE/CHAPTER: are these actually different local/regional entities (different city, different osasto/piiri, branch vs national body)? '
    : 'Focus on IDENTITY/COINCIDENCE: could the shared words be coincidental or denote a different registered org (different subtype, different legal person)? '
  return `Read the JSON file at ${file} (fields a, b, shared). Two org-name clusters were judged to be the SAME registered entity. Try hard to REFUTE that. ${t}
Set refuted=true if ANY plausible reason they are not certainly the same registered legal entity. Default refuted=true when uncertain. Reason in Finnish, one sentence.`
}

const items = Array.from({ length: n }, (_, i) => `${dir}/${pad(i)}.json`)

const results = await pipeline(
  items,
  (file) => agent(classifyPrompt(file), { label: `cls:${file.slice(-8)}`, phase: 'Classify', schema: CLASSIFY_SCHEMA })
    .then(c => ({ file, c })),
  ({ file, c }) => {
    if (!c.merge || c.confidence === 'low') return { file, c, confirmed: false, votes: [] }
    return parallel([
      () => agent(refutePrompt(file, 'locality'), { label: `ref-loc:${file.slice(-8)}`, phase: 'Refute', schema: VERDICT_SCHEMA }),
      () => agent(refutePrompt(file, 'identity'), { label: `ref-id:${file.slice(-8)}`, phase: 'Refute', schema: VERDICT_SCHEMA }),
    ]).then(votes => {
      const v = votes.filter(Boolean)
      const refuted = v.some(x => x.refuted)
      return { file, c, confirmed: !refuted && v.length === 2, votes: v }
    })
  }
)

const clean = results.filter(Boolean)
const confirmed = clean.filter(r => r.confirmed)
const rejected = clean.filter(r => !r.confirmed)
return {
  total: clean.length,
  confirmed: confirmed.length,
  rejected: rejected.length,
  confirmedPairs: confirmed.map(r => ({ from: r.c.from, to: r.c.to, conf: r.c.confidence, reason: r.c.reason })),
  rejectedSample: rejected.slice(0, 50).map(r => ({ from: r.c.from, to: r.c.to, merge: r.c.merge, fm: r.c.failuremode, reason: r.c.reason, refuted: r.votes.filter(v => v.refuted).map(v => v.reason) })),
}
