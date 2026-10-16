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
type KeyPoint = {
  topic_key: string; kind: string; proposition: string;
  source_path: string; line: number;
};
type Digest = {
  kind_counts: Record<string, number>;
  sections: { name: string; count: number; key_points: KeyPoint[] }[];
  rejected_documents: { path: string; reason: string }[];
};
type RouteProposal = {
  title: string; rationale_zh: string; basis_claim_ids: string[];
  design_zh: string; success_criteria_zh: string; falsification_zh: string;
};
type RouteData = {
  available: boolean;
  route: {
    premises_zh: string[]; proposals: RouteProposal[]; stop_rules_zh: string[];
    provider?: string; created_at?: string;
  } | null;
  review: {
    verdict: string; evaluation_zh: string; results_digest_zh: string;
    recommended_next_zh: string; evidence_refs: string[];
  } | null;
};
export type LoopStatus = {
  running: boolean; round: number; step: string; message: string;
  stop_reason: string | null; verdicts: string[]; rounds_completed: number;
};
type PacketDocument = {
  path: string; git_commit: string; source_sha256: string; numbered_text: string;
};
type ChatPacket = {
  goal: string; instructions: string; schema: object; documents: PacketDocument[];
};
type Props = { projectId: string; projectName: string; loopStatus?: LoopStatus | null };
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

export default function ResearchAgentPanel({ projectId, projectName, loopStatus }: Props) {
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
  const [digest, setDigest] = useState<Digest | null>(null);
  const [route, setRoute] = useState<RouteData | null>(null);
  const [routeText, setRouteText] = useState('');
  const [routeBusy, setRouteBusy] = useState(false);
  const [routeInfo, setRouteInfo] = useState('');

  function refreshDigest() {
    call<Digest>('/projects/' + projectId + '/research-agent/digest')
      .then(setDigest).catch(() => setDigest(null));
  }
  function loadReport() {
    call<Report>('/projects/' + projectId + '/research-agent')
      .then(setReport).catch(e => setError(String(e)));
  }
  function loadRoute() {
    call<RouteData>('/projects/' + projectId + '/research-agent/route')
      .then(setRoute).catch(() => setRoute(null));
  }

  const loopKey = (loopStatus?.stop_reason ?? '') + ':' + (loopStatus?.rounds_completed ?? 0);
  useEffect(() => {
    if (loopStatus && !loopStatus.running && loopStatus.rounds_completed > 0) {
      loadReport(); refreshDigest(); loadRoute();
    }
  }, [loopKey]);

  useEffect(() => {
    setReport(null); setPacket(null); setSelectedDoc(''); setError(''); setInfo(''); setDigest(null);
    loadReport();
    refreshDigest();
    loadRoute();
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
      refreshDigest();
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
      setReport(result); setRaw(''); refreshDigest();
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

  async function generateRoute() {
    setRouteBusy(true); setRouteInfo(''); setError('');
    try {
      setRoute(await call<RouteData>(
        '/projects/' + projectId + '/research-agent/route', 'POST',
        { model: model || undefined },
      ));
      setRouteInfo('实验路线已生成（每条提案的 basis_claim_ids 均通过存在性校验，未知 id 会被拒绝）。');
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setRouteBusy(false); }
  }

  function packIteration() {
    if (!route?.route) return;
    const pack = [
      'EpiPilot experiment route iteration for ' + projectName + '.',
      'Step 1 - Evaluate this route for feasibility, data availability and statistical risk.',
      'Step 2 - Execute the proposed experiments (write code, run backtests) and produce '
        + 'a report markdown ready to be committed into the research repository.',
      'Step 3 - Return ONLY valid JSON matching: '
        + '{"verdict":"...","evaluation_zh":"...","results_digest_zh":"...",'
        + '"recommended_next_zh":"...","evidence_refs":["path-or-url"]}',
      'All narrative fields in Simplified Chinese. Never claim significance before '
        + 'testing; never promise returns.',
      'ROUTE:\n' + JSON.stringify(route.route, null, 2),
    ].join('\n\n');
    navigator.clipboard.writeText(pack).then(
      () => setRouteInfo('迭代包已复制。粘贴到 ChatGPT Web 评估并执行；实验报告存入仓库并提交后再导入结果。'),
      () => setRouteInfo('复制失败，请手动选择内容。'),
    );
  }

  async function importReviewAndRefreshClaims() {
    if (!routeText.trim()) return;
    setRouteBusy(true); setRouteInfo(''); setError('');
    try {
      setRoute(await call<RouteData>(
        '/projects/' + projectId + '/research-agent/route/review', 'POST',
        { review_json: routeText },
      ));
      setRouteText('');
      setRouteInfo('迭代结果已入库；正在按仓库最新文档更新 claims（前提是实验报告已存入并提交）。');
      await analyze();
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setRouteBusy(false); }
  }

  async function driveChatGPT() {
    setRouteBusy(true); setRouteInfo('正在驱动 ChatGPT Web（首次使用会弹出浏览器窗口，请按提示登录）…'); setError('');
    try {
      setRoute(await call<RouteData>(
        '/projects/' + projectId + '/research-agent/route/chatgpt', 'POST', {},
      ));
      setRouteInfo('ChatGPT Web 已回传评估结果并入库；正在更新 claims…');
      await analyze();
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setRouteBusy(false); }
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
          {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(n => <option value={n} key={n}>{n}</option>)}
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
        <button className="iconbtn" aria-label="Reload report" onClick={() => { loadReport(); refreshDigest(); }}><RefreshCcw size={16}/></button>
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
        {digest && digest.sections.length > 0 && <div className="agent-digest">
          <span className="eyebrow">KEY POINTS</span>
          <h4>要点摘要</h4>
          <p className="helper">确定性派生视图：按主题归类的代表性命题，每条均标注出处；完整 claims 与 review 见下方列表。</p>
          {digest.sections.map(section => <div className="digest-section" key={section.name}>
            <strong>{section.name}<span className="badge">{section.count}</span></strong>
            <ul>{section.key_points.map(point =>
              <li key={point.topic_key + ':' + point.line}>
                <span className="badge muted">{point.topic_key.replaceAll('_', ' ')}</span>
                <p>{point.proposition}</p>
                <small className="source">{point.source_path}:{point.line}</small>
              </li>)}</ul>
          </div>)}
          {digest.rejected_documents.length > 0 && <p className="helper">
            被拒文档（引用未过逐字校验，整批拒绝）：{digest.rejected_documents.map(d => d.path).join('、')}
          </p>}
        </div>}
        <div className="agent-digest route-panel">
          <span className="eyebrow">EXPERIMENT ROUTE</span>
          <h4>实验路线迭代</h4>
          <p className="helper">基于已有 claims 生成下一算法实验路线（提案引用 claim id 校验 fail-closed）；可一键由浏览器驱动发送到 ChatGPT Web 评估并执行（首次需在弹出的浏览器中登录），也可手动复制粘贴。结果回传后更新 claims，继续下一轮。</p>
          <div className="route-actions">
            <button className="btn primary small" disabled={routeBusy} onClick={generateRoute}>生成实验路线</button>
            <button className="btn secondary small" disabled={routeBusy || !route?.route} onClick={packIteration}>复制 ChatGPT 迭代包</button>
            <button className="btn primary small" disabled={routeBusy || !route?.route} onClick={driveChatGPT}>自动发送到 ChatGPT Web</button>
          </div>
          {routeInfo && <p className="helper" role="status">{routeInfo}</p>}
          {route?.route && <div className="route-list">
            {route.route.premises_zh.length > 0 && <div className="digest-section">
              <strong>市场前提<span className="badge">{route.route.premises_zh.length}</span></strong>
              <ul>{route.route.premises_zh.map((item, i) => <li key={i}><p>{item}</p></li>)}</ul>
            </div>}
            {route.route.proposals.map(proposal => <article className="note" key={proposal.title}>
              <div className="note-badges"><span className="badge blue">{proposal.title}</span></div>
              <p>{proposal.rationale_zh}</p>
              <p className="agent-caveat">设计：{proposal.design_zh}</p>
              <p className="agent-caveat">成功标准：{proposal.success_criteria_zh}</p>
              <p className="agent-caveat">证伪条件：{proposal.falsification_zh}</p>
              <div className="source">依据 claims：{proposal.basis_claim_ids.map(id => id.slice(0, 8)).join('、')}</div>
            </article>)}
            {route.route.stop_rules_zh.length > 0 && <p className="helper">停止规则：{route.route.stop_rules_zh.join('；')}</p>}
          </div>}
          {route?.review && <div className="notice"><ShieldCheck size={16}/>
            <div>最近一次迭代回传（{route.review.verdict}）：{route.review.results_digest_zh}
            <br/>下一步建议：{route.review.recommended_next_zh}</div></div>}
          <textarea className="route-input" value={routeText} rows={4}
            onChange={e => setRouteText(e.target.value)}
            placeholder="粘贴 ChatGPT 返回的 RouteReview JSON（verdict / evaluation_zh / results_digest_zh / recommended_next_zh / evidence_refs）…"/>
          <button className="btn primary small" disabled={routeBusy || !routeText.trim()}
            onClick={importReviewAndRefreshClaims}>导入结果并更新 claims</button>
        </div>
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
