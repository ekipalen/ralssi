export const meta = {
  name: 'ray-ytunnus-resolve',
  description: 'Resolve y_tunnus for top unmatched RAY orgs via same-entity candidate matching',
  phases: [{ title: 'Resolve', detail: 'one agent per RAY org: pick same-entity candidate or known y-tunnus' }],
}

const N = args && args.n ? args.n : 300
const DIR = (args && args.dir) || '/tmp/rayc'

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['idx', 'ray_name', 'decision', 'candidate_i', 'ytunnus', 'current_name', 'confidence', 'reason'],
  properties: {
    idx: { type: 'integer' },
    ray_name: { type: 'string' },
    decision: { type: 'string', enum: ['candidate', 'known', 'none'] },
    candidate_i: { type: 'integer', description: 'index of chosen candidate, or -1' },
    ytunnus: { type: 'string', description: "Finnish business ID NNNNNNN-N, or '' " },
    current_name: { type: 'string', description: 'current official org name, or empty' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    reason: { type: 'string' },
  },
}

function prompt(i) {
  const f = `${DIR}/${String(i).padStart(3, '0')}.json`
  return `Read the file ${f}. It has a RAY-era (2000–2016) Finnish organisation name (\`ray_name\`), the total euros it received (\`eur\`), and a shortlist of \`candidates\` (current organisations from our database, each with a verified \`ytunnus\`).

Your job: determine the y-tunnus (Finnish business ID) of the SAME legal entity as \`ray_name\`.

Rules — be precise, money is attributed by this decision:
1. If exactly one candidate is the SAME legal entity as ray_name — accounting for renames (e.g. "Keskusliitto" → "Liitto", "X-seura" → "X ry"), bilingual Finnish/Swedish names, abbreviations, and minor spelling/legal-form variants — return decision="candidate", candidate_i=<its i>, ytunnus=<its ytunnus>, current_name=<candidate name>.
2. A candidate that merely shares a topic word but is a DIFFERENT entity (different town's association, different organisation entirely) is NOT a match.
3. If no candidate matches but you are confident from well-known public knowledge of this organisation's real current y-tunnus, return decision="known", candidate_i=-1, ytunnus=<NNNNNNN-N>, current_name=<current official name>. Only do this for organisations you genuinely recognise; do NOT guess digits.
4. Otherwise decision="none", candidate_i=-1, ytunnus="", current_name="".

Set confidence honestly (high only when certain of same-entity). Echo idx and ray_name from the file. Keep reason to one sentence.`
}

phase('Resolve')
const items = Array.from({ length: N }, (_, i) => i)
const results = await pipeline(
  items,
  (i) => agent(prompt(i), { label: `ray:${String(i).padStart(3, '0')}`, phase: 'Resolve', schema: SCHEMA })
)

const ok = results.filter(Boolean)
const byDec = { candidate: [], known: [], none: [] }
for (const r of ok) (byDec[r.decision] || byDec.none).push(r)
log(`resolved ${ok.length}/${N}: candidate=${byDec.candidate.length} known=${byDec.known.length} none=${byDec.none.length}`)

return {
  total: N,
  returned: ok.length,
  candidate: byDec.candidate.length,
  known: byDec.known.length,
  none: byDec.none.length,
  hits: ok.filter((r) => r.decision !== 'none' && r.ytunnus),
}
