import ResearchAgentPanel from './ResearchAgentPanel';
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { ReactFlow, Background, Controls, type Edge, type Node, Position } from '@xyflow/react';
import {
  Activity, ArrowRight, BookOpen, Check, CheckCircle2, ChevronRight, CircleAlert,
  Clock3, Copy, ExternalLink, FileText, FolderGit2, GitBranch, GitCommitHorizontal,
  LayoutDashboard, Lightbulb, Plus, RefreshCcw, Search, ShieldCheck, Sparkles, X
} from 'lucide-react';

type Candidate = {
  id: string; kind: 'observation' | 'lesson'; text: string;
  source_path: string; line: number; git_commit: string; source_sha256: string;
  review: 'pending' | 'accepted_note' | 'rejected';
};
type GitState = {
  attached: boolean; branch?: string; commit?: string;
  changed_paths?: number; worktrees?: number;
};
type EventRecord = {
  seq: number; kind: string; payload: Record<string, unknown>; occurred_at: string;
};
type Project = {
  id: string; name: string; goal: string; mode: string;
  repository_path: string | null; git_commit: string | null; git_branch: string | null;
  created_at: string; candidates: Candidate[]; events: EventRecord[]; git: GitState;
};
type Summary = Pick<Project, 'id' | 'name' | 'goal' | 'mode'>;
type View = 'overview' | 'agent' | 'evidence' | 'graph' | 'git' | 'activity';

async function api<T>(path: string, method = 'GET', body?: object): Promise<T> {
  const response = await fetch('/api' + path, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!response.ok) {
    const info = await response.json().catch(() => ({}));
    throw new Error(info.detail || 'Request failed: ' + response.status);
  }
  return response.json() as Promise<T>;
}

const nav: { view: View; name: string; icon: typeof Activity }[] = [
  { view: 'overview', name: 'Overview', icon: LayoutDashboard },
  { view: 'agent', name: 'Research Onboarding Agent', icon: Sparkles },
  { view: 'evidence', name: 'Observations & knowledge', icon: Lightbulb },
  { view: 'graph', name: 'Research graph', icon: GitBranch },
  { view: 'git', name: 'Git workspace', icon: FolderGit2 },
  { view: 'activity', name: 'Activity timeline', icon: Clock3 }
];

function Badge({ children, tone = '' }: { children: ReactNode; tone?: string }) {
  return <span className={'badge ' + tone}>{children}</span>;
}
function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty"><BookOpen size={30}/><p>{children}</p></div>;
}
function Stat({ label, value, note, icon: Icon }: {
  label: string; value: number; note: string; icon: typeof Activity;
}) {
  return <div className="stat"><div className="stat-label">{label}<Icon size={17}/></div>
    <strong>{value}</strong><small>{note}</small></div>;
}

function NewProject({ close, create }: {
  close: () => void; create: (input: {
    name: string; goal: string; mode: 'adopt' | 'create'; repository_path?: string
  }) => Promise<void>;
}) {
  const [mode, setMode] = useState<'adopt' | 'create'>('adopt');
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [path, setPath] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError('');
    try { await create({ mode, name, goal, repository_path: mode === 'adopt' ? path : undefined }); }
    catch (err) { setError(String(err instanceof Error ? err.message : err)); }
    finally { setBusy(false); }
  }
  return <div className="shade" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}>
    <form className="modal" onSubmit={submit}>
      <header className="modal-top"><div><span className="eyebrow">ONBOARD RESEARCH</span>
        <h2>Add a project</h2><p>Start a new mission or recover knowledge from existing work.</p></div>
        <button type="button" className="iconbtn" onClick={close} aria-label="Close"><X size={19}/></button></header>
      <div className="choices">
        <button type="button" className={'choice ' + (mode === 'adopt' ? 'chosen' : '')} onClick={() => setMode('adopt')}>
          <FolderGit2 size={23}/><strong>Adopt existing</strong><small>Analyze local Git history and research documents.</small>
        </button>
        <button type="button" className={'choice ' + (mode === 'create' ? 'chosen' : '')} onClick={() => setMode('create')}>
          <Plus size={23}/><strong>Start new</strong><small>Create a mission with explicit research goals.</small>
        </button>
      </div>
      <label>Project name<input value={name} required maxLength={120} placeholder="quant_etf" onChange={e => setName(e.target.value)}/></label>
      <label>Research goal<textarea value={goal} required maxLength={3000} rows={3} placeholder="What should the research discover or verify?" onChange={e => setGoal(e.target.value)}/></label>
      {mode === 'adopt' && <label>Absolute local Git repository path
        <input value={path} required placeholder="/home/user/projects/quant_etf" onChange={e => setPath(e.target.value)}/>
        <small>Local read-only Git scan. Set EPIPILOT_ALLOWED_ROOTS for other directories.</small>
      </label>}
      <div className="notice"><ShieldCheck size={17}/> Extracted observations require review. This does not write GitHub, Notion or canonical EpiPilot facts.</div>
      {error && <p role="alert" className="error"><CircleAlert size={16}/>{error}</p>}
      <div className="modal-actions"><button type="button" className="btn secondary" onClick={close}>Cancel</button>
        <button type="submit" disabled={busy} className="btn primary">{busy ? 'Scanning…' : 'Continue'}<ArrowRight size={16}/></button></div>
    </form>
  </div>;
}

function Notes({ project, filter, onFilter, review, busy }: {
  project: Project; filter: string; onFilter: (value: string) => void;
  review: (id: string, decision: 'accepted_note' | 'rejected') => Promise<void>; busy: boolean;
}) {
  const items = project.candidates.filter(n =>
    (n.text + n.source_path + n.kind).toLowerCase().includes(filter.toLowerCase())
  );
  return <section className="panel"><div className="panel-head">
    <div><span className="eyebrow">EVIDENCE INBOX</span><h3>Research observations & knowledge</h3></div>
    <Badge tone="amber">{project.candidates.filter(n => n.review === 'pending').length} pending</Badge></div>
    <div className="search"><Search size={17}/><input value={filter} onChange={e => onFilter(e.target.value)} placeholder="Search observations, findings and sources"/></div>
    <p className="helper">These are extracted snippets, not verified experimental conclusions. Every candidate is traceable to an immutable Git snapshot.</p>
    {items.length ? <div className="note-list">{items.map(item => <article className="note" key={item.id}>
      <div className="note-badges">
        <Badge tone={item.kind === 'lesson' ? 'violet' : 'blue'}>{item.kind === 'lesson' ? 'Knowledge candidate' : 'Observation'}</Badge>
        <Badge tone={item.review === 'pending' ? 'amber' : item.review === 'accepted_note' ? 'green' : 'muted'}>{item.review === 'pending' ? 'Needs review' : item.review === 'accepted_note' ? 'Reviewed note' : 'Rejected'}</Badge>
      </div><p>{item.text}</p><div className="source"><FileText size={14}/>{item.source_path}:{item.line} · {item.git_commit.slice(0, 11)}</div>
      {item.review === 'pending' && <div className="note-actions"><button className="btn quiet small" disabled={busy} onClick={() => review(item.id, 'rejected')}><X size={14}/> Reject</button>
        <button className="btn secondary small" disabled={busy} onClick={() => review(item.id, 'accepted_note')}><Check size={14}/> Keep as note</button></div>}
    </article>)}</div> : <Empty>No matching historical notes.</Empty>}
  </section>;
}

function Graph({ project }: { project: Project }) {
  const graph = useMemo(() => {
    const sources = [...new Set(project.candidates.map(c => c.source_path))].slice(0, 6);
    const nodes: Node[] = [{
      id: 'root', position: { x: 0, y: 140 }, data: { label: project.name },
      sourcePosition: Position.Right, type: 'input',
      style: { background: '#e9edff', border: '1px solid #8491ef', borderRadius: 12, width: 180, padding: 14, fontWeight: 700 }
    }];
    const edges: Edge[] = [];
    sources.forEach((source, i) => {
      const sid = 'src' + i;
      nodes.push({ id: sid, position: { x: 270, y: i * 140 }, data: { label: source },
        sourcePosition: Position.Right, targetPosition: Position.Left,
        style: { background: 'white', border: '1px solid #dfe4ed', borderRadius: 12, width: 220, padding: 12 } });
      edges.push({ id: 'r' + i, source: 'root', target: sid });
      const note = project.candidates.find(c => c.source_path === source && c.review !== 'rejected');
      if (note) {
        const nid = 'note' + i;
        nodes.push({ id: nid, position: { x: 585, y: i * 140 },
          data: { label: note.text.slice(0, 90) }, targetPosition: Position.Left, type: 'output',
          style: { background: note.review === 'accepted_note' ? '#edf8f3' : '#fff9ec',
            border: '1px solid #dfe4ed', borderRadius: 12, width: 240, padding: 12, fontSize: 11 } });
        edges.push({ id: 'n' + i, source: sid, target: nid });
      }
    });
    return { nodes, edges };
  }, [project]);
  return <section className="panel"><div className="panel-head"><div><span className="eyebrow">RESEARCH LINEAGE</span><h3>Source to observation</h3></div><Badge>Read-only</Badge></div>
    <p className="helper">A document lineage graph, not a validated causal or hypothesis network.</p>
    {project.candidates.length ? <div className="graph"><ReactFlow nodes={graph.nodes} edges={graph.edges} fitView nodesDraggable={false} nodesConnectable={false}>
      <Background color="#e4e8f0"/><Controls showInteractive={false}/></ReactFlow></div> : <Empty>Import research documents to see their lineage.</Empty>}
  </section>;
}

function GitPanel({ project }: { project: Project }) {
  const g = project.git;
  return <section className="panel"><div className="panel-head"><div><span className="eyebrow">LOCAL GIT</span><h3>Repository workspace</h3></div><Badge tone={g.attached ? 'green' : ''}>{g.attached ? 'Attached' : 'No repository'}</Badge></div>
    {g.attached ? <><div className="git-details">
      <div><span>Local path</span><code>{project.repository_path}</code></div>
      <div><span>Current branch</span><strong><GitBranch size={15}/>{g.branch}</strong></div>
      <div><span>Imported snapshot</span><code>{project.git_commit}</code></div>
      <div><span>Current HEAD</span><code>{g.commit}</code></div></div>
      <div className="git-stats"><div><strong>{g.changed_paths}</strong><span>Changed tracked paths</span></div><div><strong>{g.worktrees}</strong><span>Git worktrees</span></div></div>
      {g.commit !== project.git_commit && <div className="notice caution"><CircleAlert size={16}/> HEAD changed after import; notes still reference the original commit.</div>}
      <div className="notice"><ShieldCheck size={17}/> Read-only local Git inspection. GitHub remote actions remain in ChatGPT.</div>
    </> : <Empty>Adopt a local Git repository to inspect branches, commits and worktrees.</Empty>}
  </section>;
}

function Overview({ project }: { project: Project }) {
  const reviewed = project.candidates.filter(n => n.review === 'accepted_note').length;
  const pending = project.candidates.filter(n => n.review === 'pending').length;
  return <><div className="stats">
    <Stat label="Extracted notes" value={project.candidates.length} note="From pinned source history" icon={FileText}/>
    <Stat label="Reviewed" value={reviewed} note="Useful notes, not facts" icon={CheckCircle2}/>
    <Stat label="Needs attention" value={pending} note="Awaiting user review" icon={CircleAlert}/>
  </div>
  <section className="panel"><div className="panel-head"><div><span className="eyebrow">PROJECT INTENT</span><h3>Research mission</h3></div><Badge tone="blue">Goal</Badge></div>
    <p className="mission">{project.goal}</p>
    <div className="notice"><ShieldCheck size={16}/> A research objective is not an accomplished result. Scientific claims require independent verification.</div>
  </section>
  <section className="panel"><div className="panel-head"><div><span className="eyebrow">HISTORICAL SIGNALS</span><h3>Recent extracted observations</h3></div><Badge>{project.mode === 'adopt' ? 'Adopted snapshot' : 'New mission'}</Badge></div>
    {project.candidates.length ? project.candidates.filter(n => n.review !== 'rejected').slice(0, 5).map(n =>
      <div className="signal" key={n.id}><span className={'signal-dot ' + (n.review === 'accepted_note' ? 'checked' : '')}/><div><p>{n.text}</p><small>{n.source_path}:{n.line} · {n.review === 'pending' ? 'Unreviewed' : 'Reviewed note'}</small></div></div>)
      : <Empty>No imported observations. New projects can add evidence as research progresses.</Empty>}
  </section></>;
}

function Timeline({ project }: { project: Project }) {
  return <section className="panel"><div className="panel-head"><div><span className="eyebrow">AUDIT EVENTS</span><h3>Research workspace timeline</h3></div><Badge>{project.events.length} recent</Badge></div>
    <div className="timeline">{project.events.map(e => <div className="event" key={e.seq}><div className="event-dot"/><div><strong>{e.kind.replaceAll('_', ' ')}</strong>
      <time>{new Date(e.occurred_at).toLocaleString()}</time>
      <p>{e.kind === 'user_intent_proposed' ? String(e.payload.message) :
        e.kind === 'candidate_review' ? String(e.payload.decision) + ' · ' + String(e.payload.candidate_id).slice(0, 10) :
        'Initial research intake · ' + String(e.payload.candidate_count ?? 0) + ' extracted candidates'}</p>
    </div></div>)}</div>
  </section>;
}

function Copilot({ project, onIntent }: { project: Project | null; onIntent: (text: string) => Promise<void> }) {
  const [message, setMessage] = useState('');
  const [mode, setMode] = useState<'question' | 'intent'>('question');
  const [response, setResponse] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit() {
    if (!project || !message.trim()) return;
    setBusy(true);
    try {
      if (mode === 'intent') {
        await onIntent(message);
        setResponse('Recorded as proposed intent in the audit timeline. No constraints or tasks changed automatically.');
      } else {
        const result = await api<{prompt: string}>('/projects/' + project.id + '/chat-prompt');
        await navigator.clipboard.writeText(result.prompt + '\n\nUser question: ' + message);
        setResponse('Question and reviewed context copied. Paste it into ordinary ChatGPT Chat; no browser scraping is used.');
      }
    } catch (e) { setResponse(String(e)); }
    finally { setBusy(false); }
  }
  return <aside className="copilot">
    <div className="copilot-header"><div className="copilot-mark"><Sparkles size={19}/></div>
      <div><strong>Research Copilot</strong><small>Grounded questions & intent</small></div><span className="green-dot"/></div>
    <div className="copilot-scroll"><div className="assistant-bubble"><strong>Research coordination</strong><p>I can prepare grounded ChatGPT questions or record your research priorities for review.</p></div>
      {project ? <><div className="mini-title">CURRENT MISSION</div>
        <div className="copilot-project"><FolderGit2 size={17}/><div><strong>{project.name}</strong><small>{project.candidates.length} extracted research notes</small></div></div>
        <div className="mini-title">SUGGESTED QUESTIONS</div>
        <div className="suggestions">{['What has been verified?', 'Which assumptions remain unproven?', 'What is the next useful experiment?'].map(q =>
          <button key={q} onClick={() => {setMode('question'); setMessage(q);}}>{q}<ChevronRight size={13}/></button>)}</div>
        {response && <div className="copilot-response" role="status">{response}</div>}
      </> : <div className="hint">Choose a research project to start.</div>}
    </div>
    <div className="composer"><div className="composer-modes">
      <button className={mode === 'question' ? 'active' : ''} onClick={() => setMode('question')}>Ask ChatGPT</button>
      <button className={mode === 'intent' ? 'active' : ''} onClick={() => setMode('intent')}>Record intent</button></div>
      <textarea value={message} onChange={e => setMessage(e.target.value)} rows={3}
        placeholder={mode === 'question' ? 'Ask about hypotheses, evidence, next steps…' : 'Specify a research constraint or priority…'}/>
      <div className="sendbar"><span>{mode === 'question' ? 'Copies ChatGPT brief' : 'Creates reviewable event'}</span>
        <button disabled={!project || !message.trim() || busy} onClick={submit} aria-label="Send"><ArrowRight size={17}/></button></div>
      {mode === 'question' && <a href="https://chatgpt.com/" target="_blank" rel="noreferrer">Open ordinary ChatGPT Chat <ExternalLink size={13}/></a>}
    </div>
  </aside>;
}

export default function App() {
  const [projects, setProjects] = useState<Summary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [view, setView] = useState<View>('overview');
  const [modal, setModal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api<Summary[]>('/projects').then(items => {
      setProjects(items); if (items.length) setSelected(items[0].id);
    }).catch(e => setError(String(e)));
  }, []);
  useEffect(() => {
    if (selected) api<Project>('/projects/' + selected).then(setProject).catch(e => setError(String(e)));
    else setProject(null);
  }, [selected]);
  async function create(data: { name: string; goal: string; mode: 'adopt' | 'create'; repository_path?: string }) {
    const item = await api<Project>('/projects', 'POST', data);
    setProjects(await api<Summary[]>('/projects'));
    setProject(item); setSelected(item.id); setModal(false); setView('overview'); setError('');
  }
  async function review(id: string, decision: 'accepted_note' | 'rejected') {
    if (!project) return;
    setBusy(true);
    try { setProject(await api<Project>('/projects/' + project.id + '/candidates/' + id + '/review', 'POST', { decision })); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function intent(message: string) {
    if (!project) return;
    setProject(await api<Project>('/projects/' + project.id + '/intent', 'POST', { message }));
  }
  async function copyPrompt() {
    if (!project) return;
    try {
      const result = await api<{prompt: string}>('/projects/' + project.id + '/chat-prompt');
      await navigator.clipboard.writeText(result.prompt); setCopied(true);
    } catch (e) { setError(String(e)); }
  }
  async function refresh() {
    if (!project) return;
    setBusy(true);
    try { setProject(await api<Project>('/projects/' + project.id)); setError(''); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  return <div className="app">
    <nav className="sidebar">
      <div className="brand"><div className="brand-icon"><Activity size={23}/></div><div><strong>EpiPilot</strong><small>RESEARCH OS</small></div></div>
      <span className="sidebar-label">WORKSPACE</span>
      <button className={'nav-btn ' + (!project ? 'active' : '')} onClick={() => {setSelected(null); setProject(null);}}><LayoutDashboard size={18}/> All projects</button>
      <div className="sidebar-header"><span>PROJECTS</span><button onClick={() => setModal(true)} aria-label="New project"><Plus size={16}/></button></div>
      {projects.map(p => <button key={p.id} className={'project-btn ' + (p.id === selected ? 'active' : '')} onClick={() => {setSelected(p.id); setView('overview');}}>
        <span className="project-letter">{p.name.charAt(0).toUpperCase()}</span><span>{p.name}</span>
      </button>)}
      {!projects.length && <p className="sidebar-empty">Create your first research project.</p>}
      {project && <><span className="sidebar-label tools-label">PROJECT TOOLS</span>
        {nav.map(n => <button className={'nav-btn ' + (view === n.view ? 'active' : '')} key={n.view} onClick={() => setView(n.view)}>
          <n.icon size={17}/>{n.name}
          {n.view === 'evidence' && project.candidates.some(c => c.review === 'pending') && <span className="nav-dot"/>}
        </button>)}</>}
      <div className="sidebar-foot"><ShieldCheck size={18}/><div><strong>Local-first workspace</strong><span>Remote operations disabled</span></div></div>
    </nav>
    <main className="main">
      <header className="topbar"><div className="breadcrumb">Workspace <ChevronRight size={15}/><strong>{project?.name || 'All projects'}</strong></div>
        <div className="top-actions"><Badge tone="blue">Research preview</Badge>
          <button className="iconbtn" disabled={!project || busy} onClick={refresh} aria-label="Refresh"><RefreshCcw size={17}/></button>
          <button className="btn primary" onClick={() => setModal(true)}><Plus size={16}/> New project</button></div></header>
      <div className="content">
        {error && <div role="alert" className="error global-error"><CircleAlert size={16}/>{error}<button onClick={() => setError('')}><X size={15}/></button></div>}
        {project ? <><div className="page-head"><div><span className="eyebrow">RESEARCH PROJECT / {project.mode.toUpperCase()}</span>
          <h1>{project.name}</h1><p>{project.git.attached ? project.git.branch + ' · ' + project.git.commit?.slice(0, 12) : 'New research mission'}</p></div>
          <div className="page-actions"><button className="btn secondary" onClick={copyPrompt}><Copy size={16}/>{copied ? 'Copied brief' : 'Copy ChatGPT brief'}</button>
            <a className="btn primary" href="https://chatgpt.com/" target="_blank" rel="noreferrer">Open ChatGPT <ExternalLink size={15}/></a></div></div>
          {view !== 'overview' && <div className="section-title"><h2>{nav.find(n => n.view === view)?.name}</h2><p>Traceability first, independent verification before conclusions.</p></div>}
          {view === 'overview' && <Overview project={project}/>}
          {view === 'agent' && <ResearchAgentPanel projectId={project.id} projectName={project.name}/>}
          {view === 'evidence' && <Notes project={project} filter={filter} onFilter={setFilter} review={review} busy={busy}/>}
          {view === 'graph' && <Graph project={project}/>}
          {view === 'git' && <GitPanel project={project}/>}
          {view === 'activity' && <Timeline project={project}/>}
          <footer className="footer"><GitCommitHorizontal size={14}/> Local Git is read-only · Observations are unverified · No trading</footer>
        </> : <div className="welcome"><div className="welcome-icon"><Sparkles size={29}/></div><span className="eyebrow">WELCOME TO EPIPILOT</span>
          <h1>Your research, with a memory.</h1>
          <p>Create a mission or adopt a repository. Recover past observations, inspect evidence and record human decisions without losing provenance.</p>
          <button className="btn primary" onClick={() => setModal(true)}><Plus size={17}/> Add a project</button>
          {projects.map(p => <button key={p.id} className="welcome-project" onClick={() => setSelected(p.id)}><FolderGit2 size={18}/>{p.name}<ArrowRight size={16}/></button>)}
        </div>}
      </div>
    </main>
    <Copilot key={selected || 'none'} project={project} onIntent={intent}/>
    {modal && <NewProject close={() => setModal(false)} create={create}/>}
  </div>;
}
