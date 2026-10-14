import { useEffect, useState } from 'react';
import { AlertTriangle, BookOpen, Check, ClipboardCopy, FileSearch, LoaderCircle, RefreshCcw, ShieldCheck, Sparkles, X } from 'lucide-react';

type Citation = {
  source_path: string; line: number; exact_quote: string;
  source_sha256: string; git_commit: string;
};
type Claim = {
  id: string; kind: string; proposition: string; topic_key: string;
  stance: string; execution_status: string; scientific_status: string;
  caveat: string; citations: Citation[]; review: 'pending' | 'accepted_note' | 'rejected';
};
type Report = {
  available: boolean; claims: Claim[];
  potential_conflicts: { topic_key: string; reason: string }[];
  provider?: string; git_commit?: string; raw_claim_count?: number;
  distinct_claim_count?: number; run_id?: string; created_at?: string;
};
type PacketDocument = {
  path: string; git_commit: string; source_sha256: string; numbered_text: string;
};
type ChatPacket = {
  goal: string; instructions: string; schema: object; documents: PacketDocument[];
};
type Props = { projectId: string; projectName: string };
type ApiConfig = { configured: boolean; provider_host: string | null; model: string;
  response_format: string; key_present: boolean };

async function call<T>(path: string, method = 'GET', value?: object): Promise<T> {
  const res = await fetch('/api' + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: value === undefined ? undefined : JSON.stringify(value),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || 'Request failed');
  }
  return res.json() as Promise<T>;
}

export default function ResearchAgentPanel({ projectId, projectName }: Props) {
  const [report, setReport] = useState<Report | null>(null);
  const [packet, setPacket] = useState<ChatPacket | null>(null);
  const [selectedDoc, setSelectedDoc] = useState('');
  const [model, setModel] = useState('');
  const [config, setConfig] = useState<ApiConfig | null>(null);
  const [approved, setApproved] = useState(false);
  const [documentLimit, setDocumentLimit] = useState(4);
  const [raw, setRaw] = useState('');
  const [mode, setMode] = useState<'api' | 'chat'>('api');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');

  useEffect(() => {
    setReport(null); setPacket(null); setSelectedDoc(''); setError(''); setInfo('');
    call<Report>('/projects/' + projectId + '/research-agent').then(setReport).catch(e => setError(String(e)));
    call<ApiConfig>('/research-model/config').then(settings => { setConfig(settings); setModel(settings.model); })
      .catch(e => setError(String(e)));
    setApproved(false);
  }, [projectId]);

  async function analyze() {
    setBusy(true); setError(''); setInfo('');
    try {
      const result = await call<Report>(
        '/projects/' + projectId + '/research-agent/run', 'POST',
        { model: model || undefined, max_documents: documentLimit },
      );
      setReport(result);
      setInfo('Third-party analysis completed. Pinned Git quotes were checked, but scientific claims remain unverified.');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function loadPacket() {
    setBusy(true); setError('');
    try {
      const result = await call<ChatPacket>(
        '/projects/' + projectId + '/research-agent/chat-packet?max_documents=6',
      );
      setPacket(result);
      setSelectedDoc(result.documents[0]?.path || '');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function copyChatRequest() {
    if (!packet) return;
    const doc = packet.documents.find(item => item.path === selectedDoc);
    if (!doc) return;
    try {
      await navigator.clipboard.writeText(
        'Research onboarding for ' + projectName + '. Use ordinary ChatGPT Chat.\n'
        + packet.instructions + '\n\nGoal: ' + packet.goal
        + '\n\nReturn ONLY valid JSON matching this schema:\n'
        + JSON.stringify(packet.schema, null, 2)
        + '\n\nPinned Git source:\n' + JSON.stringify(doc, null, 2),
      );
      setInfo('Source-grounded task copied. Paste into ordinary ChatGPT Chat, then paste the returned JSON below.');
    } catch (e) { setError(String(e)); }
  }

  async function importChat() {
    if (!selectedDoc || !raw.trim()) return;
    setBusy(true); setError(''); setInfo('');
    try {
      const result = await call<Report>(
        '/projects/' + projectId + '/research-agent/chat-import', 'POST',
        { source_path: selectedDoc, response_json: raw },
      );
      setReport(result); setRaw('');
      setInfo('Response parsed, original citations checked and research notes staged. No scientific claims promoted.');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function review(claimId: string, decision: 'accepted_note' | 'rejected') {
    setBusy(true); setError('');
    try {
      const result = await call<Report>(
        '/projects/' + projectId + '/research-agent/claims/' + claimId + '/review',
        'POST', { decision },
      );
      setReport(result);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  return <section className="panel agent-workspace">
    <div className="panel-head">
      <div><span className="eyebrow">SEMANTIC INTAKE</span><h3>Research Onboarding Agent</h3></div>
      <span className="badge blue"><Sparkles size={12}/> Model-assisted</span>
    </div>
    <p className="helper">Reconstruct experiments, hypotheses, failures and reusable lessons from pinned research documents. Grounding is checked against source lines; model interpretation is not independent scientific verification.</p>
    <div className="agent-modes">
      <button className={mode === 'api' ? 'active' : ''} onClick={() => setMode('api')}>Third-party API</button>
      <button className={mode === 'chat' ? 'active' : ''} onClick={() => setMode('chat')}>Ordinary ChatGPT</button>
    </div>
    {mode === 'api' ? <div className="agent-configuration">
      <div className="agent-formline">
        <label>Provider model ID <input value={model} onChange={e => setModel(e.target.value)} placeholder="e.g. deepseek-chat or openai/gpt-4o"/></label>
        <label>Research documents <select value={documentLimit} onChange={e => setDocumentLimit(Number(e.target.value))}>
          {[1, 2, 3, 4, 5, 6].map(n => <option value={n} key={n}>{n}</option>)}
        </select></label>
      </div>
      <div className="notice"><ShieldCheck size={16}/>{config?.configured
        ? 'API configured on backend: ' + config.provider_host + ' · ' + config.response_format
        : 'Configure EPIPILOT_RESEARCH_API_BASE_URL, _API_KEY and _MODEL on the Python server.'}</div>
      <p className="helper">A bounded excerpt of the selected research documents and your research goal will be transmitted to the configured external API. Never include credentials or sensitive project records. Keys stay in server environment variables, not the browser.</p>
      <label className="agent-consent"><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)}/>
        <span>I authorize sending these research excerpts to the configured third-party model provider.</span>
      </label>
      <button className="btn primary" disabled={busy || !approved || !config?.configured || !model.trim()} onClick={analyze}>
        {busy ? <LoaderCircle className="spin" size={16}/> : <FileSearch size={16}/>}
        {busy ? 'Analyzing historical experiments…' : 'Analyze project history'}
      </button>
    </div> : <div className="agent-configuration">
      <p className="helper">Use normal ChatGPT chat to analyze one pinned document at a time. GitHub and Notion operations remain in ChatGPT; return structured JSON for local citation validation.</p>
      {!packet && <button className="btn secondary" disabled={busy} onClick={loadPacket}><BookOpen size={16}/> Prepare grounded sources</button>}
      {packet && <div className="agent-chat-flow">
        <label>Source document <select value={selectedDoc} onChange={e => setSelectedDoc(e.target.value)}>
          {packet.documents.map(d => <option key={d.path} value={d.path}>{d.path}</option>)}
        </select></label>
        <button className="btn secondary" onClick={copyChatRequest}><ClipboardCopy size={16}/> Copy ChatGPT analysis request</button>
        <textarea value={raw} onChange={e => setRaw(e.target.value)} rows={6} placeholder='Paste a ChatGPT JSON response containing {"claims":[...]}…'/>
        <button className="btn primary" disabled={busy || !raw.trim()} onClick={importChat}>Validate and import JSON</button>
      </div>}
    </div>}
    {error && <div className="error" role="alert"><AlertTriangle size={16}/>{error}</div>}
    {info && <div className="notice" role="status"><ShieldCheck size={16}/>{info}</div>}
    <div className="agent-summary">
      <div className="panel-head"><div><span className="eyebrow">RESEARCH RECONSTRUCTION</span><h3>Grounded candidates</h3></div>
        <button className="iconbtn" aria-label="Reload report" onClick={() => call<Report>('/projects/' + projectId + '/research-agent').then(setReport).catch(e => setError(String(e)))}><RefreshCcw size={16}/></button>
      </div>
      {!report?.available ? <div className="empty"><BookOpen size={27}/><p>No semantic intake yet. Use the configured third-party API or ChatGPT to prepare source-grounded claims.</p></div> : <>
        <div className="agent-stats">
          <div><strong>{report.distinct_claim_count}</strong><small>Distinct candidates</small></div>
          <div><strong>{report.raw_claim_count}</strong><small>Extracted mentions</small></div>
          <div><strong>{report.potential_conflicts.length}</strong><small>Possible conflicts</small></div>
        </div>
        <p className="helper">Run: {report.run_id?.slice(0, 8)} · {report.provider} · pinned commit {report.git_commit?.slice(0, 12)}</p>
        {report.potential_conflicts.map(c => <div className="agent-conflict" key={c.topic_key}>
          <AlertTriangle size={17}/><div><strong>Check opposing claims: {c.topic_key.replaceAll('_', ' ')}</strong><p>{c.reason}</p></div>
        </div>)}
        <div className="agent-claims">{report.claims.map(claim => <article className="note" key={claim.id}>
          <div className="note-badges"><span className="badge blue">{claim.kind}</span>
            <span className="badge">{claim.execution_status === 'completed' ? 'Execution completed' : claim.execution_status}</span>
            <span className={'badge ' + (claim.scientific_status === 'blocked' || claim.scientific_status === 'refuted' ? 'amber' : '')}>Scientific: {claim.scientific_status}</span>
            <span className={'badge ' + (claim.review === 'accepted_note' ? 'green' : claim.review === 'pending' ? 'amber' : 'muted')}>{claim.review}</span>
          </div>
          <p>{claim.proposition}</p>
          {claim.caveat && <p className="agent-caveat">Qualification: {claim.caveat}</p>}
          <div className="agent-citations">{claim.citations.map((src, index) => <div className="agent-source" key={index}>
            <small>{src.source_path}:{src.line} · {src.git_commit.slice(0, 12)}</small>
            <blockquote>{src.exact_quote}</blockquote></div>)}</div>
          {claim.review === 'pending' && <div className="note-actions">
            <button className="btn quiet small" disabled={busy} onClick={() => review(claim.id, 'rejected')}><X size={14}/> Reject</button>
            <button className="btn secondary small" disabled={busy} onClick={() => review(claim.id, 'accepted_note')}><Check size={14}/> Accept as note</button>
          </div>}
        </article>)}</div>
      </>}
    </div>
  </section>;
}
