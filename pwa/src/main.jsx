import React, { Component, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bot,
  CheckCircle2,
  CloudRain,
  Database,
  FileSearch,
  GitBranch,
  History,
  IndianRupee,
  Leaf,
  MessageSquareText,
  RefreshCw,
  Settings,
  ShieldCheck,
  Sprout,
} from 'lucide-react';

import { Badge } from './components/ui/badge';
import { Button } from './components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui/card';
import { Input } from './components/ui/input';
import './styles.css';

const routes = [
  { path: '/', label: 'Dashboard', icon: Leaf, page: DashboardPage },
  { path: '/crop-analysis', label: 'Crop Analysis', icon: Sprout, page: CropAnalysisPage },
  { path: '/market-prices', label: 'Market Prices', icon: IndianRupee, page: MarketPricesPage },
  { path: '/weather', label: 'Weather', icon: CloudRain, page: WeatherPage },
  { path: '/ai-advisor', label: 'AI Advisor', icon: Bot, page: AIAdvisorPage },
  { path: '/history', label: 'History', icon: History, page: HistoryPage },
  { path: '/settings', label: 'Settings', icon: Settings, page: SettingsPage },
];

const defaultState = {
  dashboard: null,
  stats: null,
  clusters: [],
  memory: [],
  impacts: [],
  sources: [],
  evalData: null,
  models: null,
  showcase: null,
  weather: null,
  market: null,
  health: null,
};

function apiHeaders() {
  const apiKey = localStorage.getItem('agrimesh_api_key');
  return apiKey ? { 'X-AgriMesh-API-Key': apiKey } : {};
}

async function api(path) {
  const res = await fetch(path, { headers: apiHeaders(), credentials: 'include' });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body?.error?.message || body?.detail || `${path}: ${res.status}`);
  }
  return body;
}

function useAppData() {
  const [state, setState] = useState(defaultState);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [apiKey, setApiKeyState] = useState(localStorage.getItem('agrimesh_api_key') || '');
  const [farmerId, setFarmerId] = useState(localStorage.getItem('agrimesh_farmer_id') || '');

  async function refresh() {
    setLoading(true);
    setError('');
    const farmerQuery = farmerId ? `?farmer_id=${encodeURIComponent(farmerId)}` : '';
    try {
      const [
        dashboard,
        stats,
        clusters,
        memory,
        impacts,
        sources,
        evalData,
        models,
        showcase,
        weather,
        market,
        health,
      ] = await Promise.all([
        api(`/api/farmer-dashboard${farmerQuery}`).catch((err) => ({ error: err.message })),
        api('/api/stats').catch((err) => ({ error: err.message })),
        api('/api/clusters').catch(() => ({ clusters: [] })),
        api('/api/memory/summaries').catch(() => ({ summaries: [] })),
        api('/api/impact-network').catch(() => ({ impacts: [] })),
        api('/api/sources').catch(() => ({ sources: [] })),
        api('/api/eval/latest').catch(() => null),
        api('/api/models').catch(() => null),
        api(`/api/ai/showcase${farmerQuery}`).catch(() => null),
        api('/api/weather/forecast').catch(() => null),
        api('/api/market-prices?crop=rice').catch(() => null),
        api('/health').catch(() => null),
      ]);

      if (dashboard?.farmer?.id) {
        localStorage.setItem('agrimesh_farmer_id', dashboard.farmer.id);
        setFarmerId(dashboard.farmer.id);
      }
      setState({
        dashboard: dashboard?.error ? null : dashboard,
        stats: stats?.error ? null : stats,
        clusters: clusters?.clusters || [],
        memory: memory?.summaries || [],
        impacts: impacts?.impacts || [],
        sources: sources?.sources || [],
        evalData,
        models,
        showcase,
        weather,
        market,
        health,
      });
      setError(dashboard?.error || stats?.error || '');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function setApiKey(value) {
    setApiKeyState(value);
    if (value.trim()) localStorage.setItem('agrimesh_api_key', value.trim());
    else localStorage.removeItem('agrimesh_api_key');
  }

  useEffect(() => {
    refresh();
  }, []);

  return { ...state, loading, error, refresh, apiKey, setApiKey, farmerId, setFarmerId };
}

class PageErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidUpdate(prevProps) {
    if (prevProps.locationKey !== this.props.locationKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      return <EmptyState icon={AlertTriangle} text={this.state.error.message || 'Page failed to render'} />;
    }
    return this.props.children;
  }
}

function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}

function AppShell() {
  const data = useAppData();
  const location = useLocation();

  return (
    <main className="app-shell weather-sunny">
      <section className="shell app-grid">
        <aside className="side-nav">
          <div className="brand-block nav-brand">
            <div className="brand-mark"><Leaf size={22} /></div>
            <div>
              <h1>AgentAgri</h1>
              <p>{data.dashboard?.farmer?.name || data.health?.model || 'Gemma farm advisor'}</p>
            </div>
          </div>
          <nav>
            {routes.map((item) => (
              <NavLink key={item.path} to={item.path} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                <item.icon size={17} />
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </aside>

        <section className="page-panel">
          <header className="topbar route-topbar">
            <div>
              <p className="eyebrow">{currentRouteLabel(location.pathname)}</p>
              <h2>{farmerTitle(data.dashboard)}</h2>
            </div>
            <div className="operator-controls">
              <label className="header-field">
                <span>API key</span>
                <Input value={data.apiKey} onChange={(event) => data.setApiKey(event.target.value)} />
              </label>
              <Button title="Refresh API-backed page data" variant="secondary" onClick={data.refresh} disabled={data.loading}>
                <RefreshCw className={data.loading ? 'spin' : ''} size={16} />
                Refresh
              </Button>
            </div>
          </header>

          {data.error && (
            <Card className="notice-card">
              <CardContent>
                <AlertTriangle size={18} />
                <span>{data.error}</span>
              </CardContent>
            </Card>
          )}

          <PageErrorBoundary locationKey={location.pathname}>
            <Routes>
              {routes.map((item) => <Route key={item.path} path={item.path} element={<item.page data={data} />} />)}
              <Route path="*" element={<DashboardPage data={data} />} />
            </Routes>
          </PageErrorBoundary>
        </section>
      </section>
    </main>
  );
}

function DashboardPage({ data }) {
  const dashboard = data.dashboard;
  const latest = dashboard?.advisories?.[0] || data.showcase?.latest_advisory;
  return (
    <Page title="Farm Command" loading={data.loading && !dashboard}>
      <section className="metric-grid">
        <Metric icon={Leaf} label="Farmers" value={data.stats?.farmers ?? 0} />
        <Metric icon={Sprout} label="Active crop" value={dashboard?.fields?.[0]?.active_crop?.crop_name || 'n/a'} />
        <Metric icon={AlertTriangle} label="Risk" value={latest?.risk_level || 'n/a'} />
        <Metric icon={Activity} label="Confidence" value={latest?.confidence || 'n/a'} />
        <Metric icon={Database} label="Sources" value={data.sources.length} />
        <Metric icon={GitBranch} label="Impact nodes" value={data.impacts.length} />
      </section>
      <section className="farmer-overview-grid">
        <AdvisoryCard advisory={latest} citations={data.showcase?.citations || []} />
        <SystemReadiness data={data} />
      </section>
    </Page>
  );
}

function CropAnalysisPage({ data }) {
  const field = data.dashboard?.fields?.[0];
  const latest = data.showcase?.latest_advisory || data.dashboard?.advisories?.[0];
  return (
    <Page title="Crop Analysis" loading={data.loading && !field}>
      <section className="farmer-overview-grid">
        <Card>
          <CardHeader>
            <CardTitle>{field?.active_crop?.crop_name || 'No active crop'}</CardTitle>
            <CardDescription>{field?.name || 'Field data loads from /api/farmer-dashboard'}</CardDescription>
          </CardHeader>
          <CardContent>
            <MiniChart points={(field?.ndvi || []).map((item) => item.ndvi)} />
            <MetricLine label="Stage" value={field?.active_crop?.current_stage || 'n/a'} />
            <MetricLine label="NDVI status" value={field?.ndvi_trend?.status || 'n/a'} />
            <MetricLine label="Area" value={field?.area_acres ? `${field.area_acres} acres` : 'n/a'} />
          </CardContent>
        </Card>
        <VisionPanel vision={data.showcase?.vision} advisory={latest} />
      </section>
      <section className="list-grid">
        {(field?.tasks || []).map((task) => (
          <Card key={task.id}>
            <CardContent className="metric-card">
              <CheckCircle2 size={18} />
              <div>
                <span>{task.stage}</span>
                <strong>{task.task_name}</strong>
              </div>
            </CardContent>
          </Card>
        ))}
      </section>
    </Page>
  );
}

function MarketPricesPage({ data }) {
  const market = data.market?.prices || data.dashboard?.mandi;
  const rows = market?.prices || [];
  return (
    <Page title="Market Prices" loading={data.loading && !market}>
      <section className="list-grid">
        {rows.map((row) => (
          <Card key={row.type}>
            <CardHeader>
              <CardTitle>{row.type}</CardTitle>
              <CardDescription>{market.district || data.market?.district || 'district from API'} · {row.unit}</CardDescription>
            </CardHeader>
            <CardContent>
              {(row.history || []).slice(0, 5).map((entry) => (
                <MetricLine key={entry.date} label={entry.date} value={`Rs ${entry.modal}`} />
              ))}
            </CardContent>
          </Card>
        ))}
        {!rows.length && <EmptyState icon={IndianRupee} text="No market rows returned by the API." />}
      </section>
    </Page>
  );
}

function WeatherPage({ data }) {
  const weather = data.weather || data.dashboard?.weather;
  const forecast = weather?.forecast || [];
  return (
    <Page title="Weather" loading={data.loading && !weather}>
      <section className="eval-grid">
        {forecast.map((day) => (
          <Card key={day.date}>
            <CardContent className="metric-card">
              <CloudRain size={18} />
              <div>
                <span>{day.date}</span>
                <strong>{day.condition || `${day.rainfall_mm || 0} mm rain`}</strong>
              </div>
            </CardContent>
          </Card>
        ))}
        {!forecast.length && <EmptyState icon={CloudRain} text="No forecast rows returned by the API." />}
      </section>
    </Page>
  );
}

function AIAdvisorPage({ data }) {
  const showcase = data.showcase || {};
  const models = data.models || showcase.models;
  const [selectedModel, setSelectedModel] = useState(models?.current || '');
  const latest = showcase.latest_advisory;

  useEffect(() => {
    if (models?.current && !selectedModel) setSelectedModel(models.current);
  }, [models, selectedModel]);

  return (
    <Page title="AI Advisor" loading={data.loading && !showcase}>
      <section className="farmer-overview-grid">
        <Card>
          <CardHeader>
            <div className="row-between">
              <CardTitle>Model Toggle</CardTitle>
              <Badge>{latest?.latency_ms ? `${latest.latency_ms} ms` : 'no latency yet'}</Badge>
            </div>
            <CardDescription>{latest?.model_used || models?.current || 'Model metadata loads from /api/models'}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="segmented-row">
              {(models?.options || showcase.models?.options || []).map((model) => {
                const id = typeof model === 'string' ? model : model.id;
                const label = typeof model === 'string' ? model : model.label;
                return (
                  <Button key={id} variant={selectedModel === id ? 'default' : 'secondary'} onClick={() => setSelectedModel(id)}>
                    <Bot size={16} />
                    {label}
                  </Button>
                );
              })}
            </div>
            <MetricLine label="Selected model" value={selectedModel || 'n/a'} />
            <MetricLine label="Confidence" value={latest?.confidence || 'n/a'} />
            <MetricLine label="Retrieval path" value={latest?.retrieval_path || 'n/a'} />
          </CardContent>
        </Card>
        <ReasoningPanel trace={showcase.reasoning_trace || []} />
      </section>
      <section className="farmer-overview-grid">
        <ToolCallPanel tools={showcase.tool_calls || []} />
        <CitationPanel citations={showcase.citations || []} />
      </section>
    </Page>
  );
}

function HistoryPage({ data }) {
  const advisories = data.showcase?.history || data.dashboard?.advisories || [];
  return (
    <Page title="History" loading={data.loading && !advisories.length}>
      <section className="list-grid">
        {advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} compact />)}
        {!advisories.length && <EmptyState icon={History} text="No advisory history returned by the API." />}
      </section>
    </Page>
  );
}

function SettingsPage({ data }) {
  return (
    <Page title="Settings" loading={false}>
      <section className="farmer-overview-grid">
        <Card>
          <CardHeader>
            <CardTitle>Runtime</CardTitle>
            <CardDescription>Read-only configuration exposed by API health and model endpoints.</CardDescription>
          </CardHeader>
          <CardContent>
            <MetricLine label="Environment" value={data.health?.environment || 'n/a'} />
            <MetricLine label="Health" value={data.health?.status || 'n/a'} />
            <MetricLine label="Primary model" value={data.models?.current || 'n/a'} />
            <MetricLine label="Fallback model" value={data.models?.fallback || 'n/a'} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Evidence Registry</CardTitle>
            <CardDescription>{data.sources.length} sources available to citations.</CardDescription>
          </CardHeader>
          <CardContent>
            {data.sources.slice(0, 6).map((source) => (
              <MetricLine key={source.id} label={source.source_name} value={source.is_official ? 'official' : source.trust_level} />
            ))}
          </CardContent>
        </Card>
      </section>
    </Page>
  );
}

function Page({ title, loading, children }) {
  if (loading) return <LoadingState title={title} />;
  return <section className="page-stack">{children}</section>;
}

function Metric({ icon: Icon, label, value }) {
  return (
    <Card>
      <CardContent className="metric-card">
        <Icon size={18} />
        <div>
          <span>{label}</span>
          <strong>{String(value)}</strong>
        </div>
      </CardContent>
    </Card>
  );
}

function AdvisoryCard({ advisory, citations = [], compact = false }) {
  if (!advisory) return <EmptyState icon={MessageSquareText} text="No advisory returned by the API." />;
  return (
    <Card className={compact ? '' : 'wide-card'}>
      <CardHeader>
        <div className="row-between">
          <CardTitle>{advisory.risk_level || 'Advisory'}</CardTitle>
          <Badge variant={riskVariant(advisory.risk_level)}>{advisory.confidence || 'n/a'}</Badge>
        </div>
        <CardDescription>{advisory.contextualization || advisory.created_at || 'Verified advisory details'}</CardDescription>
      </CardHeader>
      <CardContent>
        <ActionList items={advisory.actions_text || []} />
        <WarningList items={advisory.warnings_text || []} />
        {!!citations.length && (
          <div className="citation-strip">
            {citations.slice(0, 4).map((citation) => (
              <Badge key={citation.id} variant={citation.is_official ? 'success' : 'outline'}>
                {citation.source_name}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SystemReadiness({ data }) {
  const checks = [
    ['API health', Boolean(data.health), Activity],
    ['Farmer data', Boolean(data.dashboard?.farmer), Leaf],
    ['Evidence', data.sources.length > 0, FileSearch],
    ['Memory', data.memory.length > 0 || (data.stats?.memory_atoms || 0) > 0, Database],
    ['Impact graph', data.impacts.length > 0 || (data.stats?.action_impacts || 0) >= 0, GitBranch],
    ['Safety eval', Boolean(data.evalData), ShieldCheck],
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Readiness</CardTitle>
        <CardDescription>Derived from live API responses.</CardDescription>
      </CardHeader>
      <CardContent className="readiness-list">
        {checks.map(([label, ok, Icon]) => (
          <div className="readiness-item" key={label}>
            <Icon size={16} />
            <span>{label}</span>
            <Badge variant={ok ? 'success' : 'warning'}>{ok ? 'ready' : 'check'}</Badge>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function VisionPanel({ vision, advisory }) {
  const imageSrc = normalizeImagePath(vision?.image_path);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Vision Analysis</CardTitle>
        <CardDescription>{vision?.confidence ? `${Math.round(vision.confidence * 100)}% confidence` : advisory?.confidence || 'No photo confidence yet'}</CardDescription>
      </CardHeader>
      <CardContent>
        {imageSrc && <img className="image-thumb" src={imageSrc} alt="Crop observation thumbnail" />}
        <p className="body-copy">{vision?.analysis ? JSON.stringify(vision.analysis) : 'Photo analysis appears here when observations include an image.'}</p>
      </CardContent>
    </Card>
  );
}

function ReasoningPanel({ trace }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Reasoning Summary</CardTitle>
        <CardDescription>Decision trace without exposing hidden model chain-of-thought.</CardDescription>
      </CardHeader>
      <CardContent>
        {(trace.length ? trace : [{ step: 'No advisory yet', summary: 'Run an advisory to populate the model trace.' }]).map((item) => (
          <div className="memory-item" key={item.step}>
            <strong>{item.step}</strong>
            <span>{item.summary}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function ToolCallPanel({ tools }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Tool Calls</CardTitle>
        <CardDescription>{tools.length} tools invoked for the latest advisory.</CardDescription>
      </CardHeader>
      <CardContent>
        {(tools.length ? tools : [{ tool_name: 'no_tool', status: 'not used' }]).map((tool) => (
          <div className="tool-node" key={tool.tool_name}>
            <Bot size={16} />
            <strong>{tool.tool_name}</strong>
            <Badge variant={tool.status === 'used' ? 'success' : 'outline'}>{tool.status}</Badge>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function CitationPanel({ citations }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Inline Citations</CardTitle>
        <CardDescription>Sources attached to the latest advisory.</CardDescription>
      </CardHeader>
      <CardContent>
        {(citations.length ? citations : [{ id: 'none', source_name: 'No citations yet', trust_level: 'n/a' }]).map((citation) => (
          <div className="source-row compact-source" key={citation.id}>
            <div>
              <strong>{citation.source_name}</strong>
              <span>{citation.context || citation.snapshot || citation.source_type || 'No citation context'}</span>
            </div>
            <Badge variant={citation.is_official ? 'success' : 'outline'}>{citation.trust_level || 'n/a'}</Badge>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function LoadingState({ title }) {
  return (
    <Card>
      <CardContent className="empty-state">
        <RefreshCw className="spin" size={22} />
        <span>{title ? `Loading ${title}` : 'Loading live AgentAgri state'}</span>
      </CardContent>
    </Card>
  );
}

function EmptyState({ icon: Icon, text }) {
  return (
    <Card>
      <CardContent className="empty-state">
        <Icon size={24} />
        <span>{text}</span>
      </CardContent>
    </Card>
  );
}

function ActionList({ items = [] }) {
  if (!items.length) return null;
  return <ol className="action-list">{items.slice(0, 5).map((item) => <li key={item}>{item}</li>)}</ol>;
}

function WarningList({ items = [] }) {
  if (!items.length) return null;
  return <ul className="warning-list">{items.slice(0, 4).map((item) => <li key={item}>{item}</li>)}</ul>;
}

function MiniChart({ points }) {
  const safe = points?.length ? points : [0.4, 0.42, 0.44, 0.45];
  const max = Math.max(...safe);
  const min = Math.min(...safe);
  const range = max - min || 1;
  const path = safe.map((point, idx) => {
    const x = (idx / Math.max(1, safe.length - 1)) * 100;
    const y = 90 - ((point - min) / range) * 70;
    return `${idx === 0 ? 'M' : 'L'} ${x} ${y}`;
  }).join(' ');
  return <svg className="mini-chart" viewBox="0 0 100 100" preserveAspectRatio="none"><path d={path} /></svg>;
}

function MetricLine({ label, value }) {
  return <div className="metric-line"><span>{label}</span><strong>{value}</strong></div>;
}

function currentRouteLabel(pathname) {
  return routes.find((item) => item.path === pathname)?.label || 'Dashboard';
}

function farmerTitle(dashboard) {
  const farmer = dashboard?.farmer;
  if (!farmer) return 'Production farmer workspace';
  return [farmer.name, farmer.village, farmer.district].filter(Boolean).join(' · ');
}

function riskVariant(value) {
  if (value === 'ESCALATE') return 'destructive';
  if (value === 'PREVENTIVE_ACTION') return 'warning';
  if (value === 'WATCH') return 'default';
  return 'outline';
}

function normalizeImagePath(path) {
  if (!path) return '';
  if (path.startsWith('http') || path.startsWith('/')) return path;
  return '';
}

createRoot(document.getElementById('root')).render(<App />);

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
