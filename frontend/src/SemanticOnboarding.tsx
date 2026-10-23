import { useEffect, useState } from 'react';

type Claim = {
  id: string;
  kind: string;
  proposition: string;
  topic_key: string;
  scientific_status: string;
  execution_status: string;
  review: 'pending' | 'accepted_note' | 'rejected';
  caveat: string;
  citations: { source_path: string; line: number; exact_quote: string }[];
};
type Report = {
  available: boolean; claims: Claim[];
  distinct_claim_count?: number;
  potential_conflicts: { topic_key: string; reason: string }[];
};
type Config = {
  configured: boolean; provider_host: string | null;
  model: string; response_format: string;
};
type Packet = {
  goal: string; instructions: string; schema: object;
  documents: { path: string; numbered_text: string }[];
};
type Props = { projectId: string; projectName: string };

async function request<T>(url: string, method = 'GET', body?: object): Promise<T> {
  const response = await fetch('/api' + url, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(String(data.detail || ('HTTP ' + response.status)));
  }
  return response.json() as Promise<T>;
}

export default function SemanticOnboarding({ projectId, projectName }: Props) {
  const [report, setReport] = useState<Report | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [packet, setPacket] = useState<Packet | null>(null);
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [selectedSource, setSelectedSource] = useState('');
  const [manualResponse, setManualResponse] = useState('');

  async function refresh() {
    try {
      const [next, nextConfig] = await Promise.all([
        request<Report>('/projects/' + projectId + '/research-agent'),
        request<Config>('/research-model/config'),
      ]);
      setReport(next);
      setConfig(nextConfig);
    } catch (e) { setError(String(e)); }
  }

  useEffect(() => {
    setApproved(false); setPacket(null); setSelectedSource('');
    setReport(null); setError(''); setNotice('');
    void refresh();
  }, [projectId]);

  async function loadPacket() {
    setError('');
    try {
      const next = await request<Packet>('/projects/' + projectId + '/research-agent/chat-packet');
      setPacket(next); setSelectedSource(next.documents[0]?.path || '');
    } catch (e) { setError(String(e)); }
  }

  async function runModel() {
    if (!approved || !config?.configured) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await request('/projects/' + projectId + '/research-agent/run', 'POST', {
        consent: true, max_documents: 4
      });
      setNotice('Source-anchored candidates are ready for manual review.');
      await refresh();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function importBatch() {
    if (!selectedSource || !manualResponse.trim()) return;
    setBusy(true); setError('');
    try {
      await request('/projects/' + projectId + '/research-agent/chat-import', 'POST', {
        source_path: selectedSource, response_json: manualResponse
      });
      setNotice('Imported and checked source quotes. No canonical claims were created.');
      setManualResponse('');
      await refresh();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function review(id: string, decision: 'accepted_note' | 'rejected') {
    setBusy(true); setError('');
    try {
      await request(
        '/projects/' + projectId + '/research-agent/claims/' + id + '/review',
        'POST', { decision }
      );
      await refresh();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  return <section className="semantic-onboarding">
    <h3>Source-grounded research onboarding · {projectName}</h3>
    <p>Extract observations, contradictions, and falsifiable claims from pinned Git sources.
      Every extracted claim remains a <strong>candidate note</strong>; model output never
      becomes verified scientific evidence without a separate v2 experiment.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <div className="semantic-controls">
      <button className="btn secondary" disabled={busy} onClick={loadPacket}>Prepare manual source packet</button>
      <button className="btn secondary" disabled={busy} onClick={refresh}>Refresh candidates</button>
    </div>
    {packet && <div className="semantic-pane">
      <h4>Manual extraction: no automatic disclosure</h4>
      <p>Copy a pinned source packet into your preferred research agent, request a JSON
        result conforming to the schema, then paste it below.</p>
      <label>Source document
        <select value={selectedSource} onChange={e => setSelectedSource(e.target.value)}>
          {packet.documents.map(d => <option value={d.path} key={d.path}>{d.path}</option>)}
        </select>
      </label>
      <button className="btn secondary" onClick={() => {
        const d = packet.documents.find(x => x.path === selectedSource);
        if (d) void navigator.clipboard.writeText(JSON.stringify({
          goal: packet.goal, instructions: packet.instructions,
          schema: packet.schema, document: d
        }, null, 2));
      }}>Copy source and extraction schema</button>
      <textarea rows={5} placeholder="Paste source-grounded JSON response"
        value={manualResponse} onChange={e => setManualResponse(e.target.value)} />
      <button className="btn primary" disabled={!manualResponse.trim() || busy} onClick={importBatch}>
        Validate and stage candidate claims
      </button>
    </div>}
    <div className="semantic-pane">
      <h4>Optional third-party extraction</h4>
      <p>{config?.configured ? (
        <>Configured provider: {config.provider_host} · {config.model}</>
      ) : 'No third-party model configured. Manual extraction remains available.'}</p>
      <label><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)} />
        I explicitly authorize sharing bounded excerpts from this adopted repository
        with the configured third-party model provider.</label>
      <button className="btn primary" disabled={!approved || !config?.configured || busy}
        onClick={runModel}>{busy ? 'Processing...' : 'Analyze pinned history'}</button>
    </div>
    <div className="semantic-pane">
      <h4>Reviewable claims ({report?.claims.length || 0})</h4>
      {(report?.potential_conflicts || []).length > 0 && <p>
        Potential contradictions: {report?.potential_conflicts.map(x => x.topic_key).join(', ')}
      </p>}
      {(report?.claims || []).map(claim => <article key={claim.id} className="semantic-claim">
        <strong>{claim.proposition}</strong>
        <p>{claim.kind} · scientific status: {claim.scientific_status} · {claim.topic_key}
          · review: {claim.review}</p>
        {claim.caveat && <p>{claim.caveat}</p>}
        {claim.citations.map((citation, i) => <blockquote key={i}>
          {citation.source_path}:{citation.line} — {citation.exact_quote}
        </blockquote>)}
        <div className="semantic-controls">
          <button className="btn secondary" disabled={busy} onClick={() => review(claim.id, 'accepted_note')}>
            Accept as note
          </button>
          <button className="btn secondary" disabled={busy} onClick={() => review(claim.id, 'rejected')}>
            Reject
          </button>
        </div>
      </article>)}
      {!report?.claims.length && <p>No grounded claims extracted yet.</p>}
    </div>
  </section>;
}
