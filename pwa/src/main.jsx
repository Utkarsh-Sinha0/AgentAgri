import React, { Component, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bot,
  Check,
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
  X,
} from 'lucide-react';

import { Badge } from './components/ui/badge';
import { Button } from './components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui/card';
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

function readDashboardToken() {
  if (typeof window === 'undefined') return '';
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get('t');
  if (fromQuery) {
    try {
      localStorage.setItem('agrimesh_dashboard_token', fromQuery);
    } catch (_) {}
    return fromQuery;
  }
  try {
    return localStorage.getItem('agrimesh_dashboard_token') || '';
  } catch (_) {
    return '';
  }
}

function useAppData() {
  const [state, setState] = useState(defaultState);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [apiKey, setApiKeyState] = useState(localStorage.getItem('agrimesh_api_key') || '');
  const [farmerId, setFarmerId] = useState(localStorage.getItem('agrimesh_farmer_id') || '');
  const [dashboardToken, setDashboardToken] = useState(readDashboardToken());

  async function refresh() {
    setLoading(true);
    setError('');
    const farmerQuery = farmerId ? `?farmer_id=${encodeURIComponent(farmerId)}` : '';
    // Prefer the signed share token minted by the Telegram bot when present.
    const dashboardPath = dashboardToken
      ? `/api/v1/dashboard/${encodeURIComponent(dashboardToken)}`
      : `/api/farmer-dashboard${farmerQuery}`;
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
        api(dashboardPath).catch((err) => ({ error: err.message })),
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
    // Live mode: when arriving via the Telegram bot's signed link, poll every
    // 5s so the dashboard mirrors the latest bot interactions in near-real-time.
    if (!dashboardToken) return undefined;
    const id = setInterval(() => {
      refresh();
    }, 5000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dashboardToken]);

  return {
    ...state,
    loading,
    error,
    refresh,
    apiKey,
    setApiKey,
    farmerId,
    setFarmerId,
    dashboardToken,
    setDashboardToken,
  };
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
    <main className="app-shell">
      <section className="app-grid">
        <aside className="side-nav">
          <div className="nav-brand">
            <div className="brand-mark"><Leaf size={20} /></div>
            <div>
              <h1>AgriMesh</h1>
              <p>{data.health?.model || 'Farm Advisor'}</p>
            </div>
          </div>
          <nav>
            {routes.map((item) => (
              <NavLink key={item.path} to={item.path} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                <item.icon size={18} />
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </aside>

        <section className="page-panel">
          <header className="route-topbar">
            <div>
              <p className="eyebrow">{currentRouteLabel(location.pathname)}</p>
              <h2>{farmerTitle(data.dashboard)}</h2>
            </div>
            <div className="operator-controls">
              <Button variant="secondary" onClick={data.refresh} disabled={data.loading}>
                <RefreshCw className={data.loading ? 'spin' : ''} size={16} />
                Refresh
              </Button>
            </div>
          </header>

          {data.error && (
            <div style={{ padding: '0 32px' }}>
              <Card className="notice-card">
                <CardContent>
                  <AlertTriangle size={18} />
                  <span>{data.error}</span>
                </CardContent>
              </Card>
            </div>
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

/* ═══════════════════════════════════════════
   Dashboard Page
   ═══════════════════════════════════════════ */

function DashboardPage({ data }) {
  const dashboard = data.dashboard;
  const farmer = dashboard?.farmer;
  const field = dashboard?.fields?.[0];
  const latest = dashboard?.advisories?.[0] || data.showcase?.latest_advisory;

  return (
    <Page title="Farm Command" loading={data.loading && !dashboard}>
      {/* Hero */}
      <div className="dashboard-hero">
        <div className="hero-content">
          <h2>{farmer?.name || 'Welcome to AgriMesh'}</h2>
          <p>{farmer ? [farmer.village, farmer.district, farmer.state].filter(Boolean).join(', ') : 'AI-powered agricultural advisory for smallholder farmers'}</p>
          <div className="hero-badges">
            {field?.active_crop?.crop_name && (
              <span className="hero-badge hero-badge-green">
                <Sprout size={13} />
                {field.active_crop.crop_name} &middot; {field.active_crop.current_stage || 'growing'}
              </span>
            )}
            {latest?.risk_level && (
              <span className={`hero-badge ${latest.risk_level === 'ESCALATE' ? 'hero-badge-terracotta' : 'hero-badge-amber'}`}>
                <AlertTriangle size={13} />
                {latest.risk_level}
              </span>
            )}
            {field?.area_acres && (
              <span className="hero-badge hero-badge-green">
                {field.area_acres} acres
              </span>
            )}
          </div>
        </div>
        <div className="hero-status">
          <div className="hero-status-pill">
            <span className={`dot ${data.health?.status === 'healthy' ? 'dot-green' : 'dot-amber'}`} />
            {data.health?.status === 'healthy' ? 'System Online' : 'Checking...'}
          </div>
          <div className="hero-status-pill">
            <Bot size={14} />
            {data.health?.model || 'Gemma 4'}
          </div>
        </div>
      </div>

      {/* Metrics */}
      <div className="section-label">Overview</div>
      <section className="metric-grid">
        <MetricCard icon={Leaf} iconStyle="green" label="Farmers" value={data.stats?.farmers ?? 0} />
        <MetricCard icon={Sprout} iconStyle="green" label="Active Crop" value={field?.active_crop?.crop_name || 'n/a'} />
        <MetricCard icon={AlertTriangle} iconStyle="amber" label="Risk Level" value={latest?.risk_level || 'n/a'} />
        <MetricCard icon={Activity} iconStyle="terracotta" label="Confidence" value={latest?.confidence || 'n/a'} />
        <MetricCard icon={Database} iconStyle="blue" label="Sources" value={data.sources.length} />
        <MetricCard icon={GitBranch} iconStyle="blue" label="Impact Nodes" value={data.impacts.length} />
      </section>

      {/* Readiness */}
      <div className="section-label">System Readiness</div>
      <ReadinessStrip data={data} />

      {/* Advisory + Details */}
      <div className="section-label" style={{ marginTop: 8 }}>Latest Advisory</div>
      <section className="overview-grid">
        <AdvisoryCard advisory={latest} citations={data.showcase?.citations || []} />
        <SystemReadiness data={data} />
      </section>
    </Page>
  );
}

/* ═══════════════════════════════════════════
   Crop Analysis Page
   ═══════════════════════════════════════════ */

function CropAnalysisPage({ data }) {
  const field = data.dashboard?.fields?.[0];
  const latest = data.showcase?.latest_advisory || data.dashboard?.advisories?.[0];
  return (
    <Page title="Crop Analysis" loading={data.loading && !field}>
      <section className="farmer-overview-grid">
        <Card className="card-accent-green">
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
      {(field?.tasks || []).length > 0 && (
        <>
          <div className="section-label">Crop Tasks</div>
          <section className="list-grid">
            {field.tasks.map((task) => (
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
        </>
      )}
    </Page>
  );
}

/* ═══════════════════════════════════════════
   Market Prices Page
   ═══════════════════════════════════════════ */

function MarketPricesPage({ data }) {
  const market = data.market?.prices || data.dashboard?.mandi;
  const rows = market?.prices || [];
  return (
    <Page title="Market Prices" loading={data.loading && !market}>
      <section className="list-grid">
        {rows.map((row) => (
          <Card key={row.type} className="card-accent-amber">
            <CardHeader>
              <CardTitle>{row.type}</CardTitle>
              <CardDescription>{market.district || data.market?.district || 'district from API'} &middot; {row.unit}</CardDescription>
            </CardHeader>
            <CardContent>
              {(row.history || []).slice(0, 5).map((entry) => (
                <MetricLine key={entry.date} label={entry.date} value={`Rs ${entry.modal}`} />
              ))}
            </CardContent>
          </Card>
        ))}
        {!rows.length && <EmptyState icon={IndianRupee} text="No market prices available yet. Prices appear when the mandi API returns data." hint="Try refreshing or check your API connection." />}
      </section>
    </Page>
  );
}

/* ═══════════════════════════════════════════
   Weather Page
   ═══════════════════════════════════════════ */

function WeatherPage({ data }) {
  const weather = data.weather || data.dashboard?.weather;
  const forecast = weather?.forecast || [];
  return (
    <Page title="Weather" loading={data.loading && !weather}>
      <section className="eval-grid">
        {forecast.map((day) => (
          <Card key={day.date}>
            <CardContent className="weather-card-content">
              <div className="weather-temp-circle">
                <CloudRain size={22} />
              </div>
              <div className="weather-detail">
                <span>{day.date}</span>
                <strong>{day.condition || `${day.rainfall_mm || 0} mm rain`}</strong>
              </div>
            </CardContent>
          </Card>
        ))}
        {!forecast.length && <EmptyState icon={CloudRain} text="No weather forecast available. Forecast data appears when the weather service responds." hint="Ensure MCP weather server is running on port 9001." />}
      </section>
    </Page>
  );
}

/* ═══════════════════════════════════════════
   AI Advisor Page
   ═══════════════════════════════════════════ */

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
        <Card className="card-accent-blue">
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

/* ═══════════════════════════════════════════
   History Page
   ═══════════════════════════════════════════ */

function HistoryPage({ data }) {
  const advisories = data.showcase?.history || data.dashboard?.advisories || [];
  return (
    <Page title="History" loading={data.loading && !advisories.length}>
      <section className="list-grid">
        {advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} compact />)}
        {!advisories.length && <EmptyState icon={History} text="No advisory history yet. Advisories appear after the AI agent processes farmer queries." hint="Send a message to the Telegram bot to generate your first advisory." />}
      </section>
    </Page>
  );
}

/* ═══════════════════════════════════════════
   Settings Page
   ═══════════════════════════════════════════ */

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

/* ═══════════════════════════════════════════
   Shared Components
   ═══════════════════════════════════════════ */

function Page({ title, loading, children }) {
  if (loading) return <LoadingState title={title} />;
  return <section className="page-stack">{children}</section>;
}

function MetricCard({ icon: Icon, iconStyle, label, value }) {
  return (
    <Card>
      <CardContent className="metric-card-v2">
        <div className={`metric-icon metric-icon-${iconStyle}`}>
          <Icon size={20} />
        </div>
        <div className="metric-text">
          <span>{label}</span>
          <strong>{String(value)}</strong>
        </div>
      </CardContent>
    </Card>
  );
}

function ReadinessStrip({ data }) {
  const checks = [
    ['API Health', Boolean(data.health), Activity],
    ['Farmer Data', Boolean(data.dashboard?.farmer), Leaf],
    ['Evidence', data.sources.length > 0, FileSearch],
    ['Memory', data.memory.length > 0 || (data.stats?.memory_atoms || 0) > 0, Database],
    ['Impact Graph', data.impacts.length > 0 || (data.stats?.action_impacts || 0) >= 0, GitBranch],
    ['Safety Eval', Boolean(data.evalData), ShieldCheck],
  ];
  return (
    <div className="readiness-grid">
      {checks.map(([label, ok, Icon]) => (
        <div className="readiness-item" key={label}>
          <div className={`readiness-check ${ok ? 'readiness-check-ok' : 'readiness-check-warn'}`}>
            {ok ? <Check size={13} /> : <X size={13} />}
          </div>
          <span>{label}</span>
          <Icon size={15} className="readiness-type-icon" style={{ marginLeft: 'auto' }} />
        </div>
      ))}
    </div>
  );
}

function AdvisoryCard({ advisory, citations = [], compact = false }) {
  if (!advisory) return <EmptyState icon={MessageSquareText} text="No advisory available. The AI agent generates advisories when farmers ask questions." hint="Use the Telegram bot or send a test query." />;
  return (
    <Card className={`${compact ? '' : 'wide-card'} ${riskAccent(advisory.risk_level)}`}>
      <CardHeader>
        <div className="advisory-header">
          <CardTitle>{advisory.contextualization || 'Advisory'}</CardTitle>
          <span className={`risk-indicator ${riskClass(advisory.risk_level)}`}>
            {advisory.risk_level || 'info'}
          </span>
        </div>
        <CardDescription>
          {advisory.created_at || 'Verified advisory details'}
          {advisory.confidence && ` · Confidence: ${advisory.confidence}`}
        </CardDescription>
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
        <CardTitle>Detailed Readiness</CardTitle>
        <CardDescription>Derived from live API responses.</CardDescription>
      </CardHeader>
      <CardContent className="readiness-list">
        {checks.map(([label, ok, Icon]) => (
          <div className="readiness-item" key={label}>
            <div className={`readiness-check ${ok ? 'readiness-check-ok' : 'readiness-check-warn'}`}>
              {ok ? <Check size={13} /> : <X size={13} />}
            </div>
            <Icon size={16} className="readiness-type-icon" />
            <span>{label}</span>
            <Badge variant={ok ? 'success' : 'warning'} style={{ marginLeft: 'auto' }}>{ok ? 'ready' : 'check'}</Badge>
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
    <section className="page-stack">
      <Card>
        <CardContent className="empty-state">
          <RefreshCw className="spin" size={24} />
          <span>{title ? `Loading ${title}...` : 'Loading live AgriMesh state...'}</span>
        </CardContent>
      </Card>
    </section>
  );
}

function EmptyState({ icon: Icon, text, hint }) {
  return (
    <Card>
      <CardContent className="empty-state">
        <Icon size={32} />
        <span>{text}</span>
        {hint && <span className="empty-state-hint">{hint}</span>}
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

/* ═══════════════════════════════════════════
   Helpers
   ═══════════════════════════════════════════ */

function currentRouteLabel(pathname) {
  return routes.find((item) => item.path === pathname)?.label || 'Dashboard';
}

function farmerTitle(dashboard) {
  const farmer = dashboard?.farmer;
  if (!farmer) return 'Farmer Workspace';
  return [farmer.name, farmer.village, farmer.district].filter(Boolean).join(' · ');
}

function riskClass(value) {
  if (value === 'ESCALATE') return 'risk-escalate';
  if (value === 'PREVENTIVE_ACTION') return 'risk-preventive';
  if (value === 'WATCH') return 'risk-watch';
  return 'risk-default';
}

function riskAccent(value) {
  if (value === 'ESCALATE') return 'card-accent-red';
  if (value === 'PREVENTIVE_ACTION') return 'card-accent-amber';
  if (value === 'WATCH') return 'card-accent-blue';
  return '';
}

function normalizeImagePath(path) {
  if (!path) return '';
  if (path.startsWith('http') || path.startsWith('/')) return path;
  return '';
}

/* ═══════════════════════════════════════════
   Mount
   ═══════════════════════════════════════════ */

createRoot(document.getElementById('root')).render(<App />);

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
