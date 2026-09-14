import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiJson } from '../services/http';
import { SiteFooter } from '../components/SiteFooter';
import './account.css';

/**
 * The founder's read-only view of the beta: `/admin`.
 *
 * Draws whatever `GET /api/admin/overview` answers and nothing else. The
 * server decides who is an admin (ADMIN_EMAILS) and refuses everyone else
 * with 401/403; this page renders that refusal as a blocked state. There is
 * no client-side allowlist and no write action anywhere on the page.
 */

type Latency = { samples: number; avg_ms: number | null; p95_ms: number | null };
type SourceCounts = { games: number; analysed: number; failed: number };
type Metric = number | string | null;

interface Overview {
    generated_at: number;
    database: boolean;
    accounts: 'not_tracked' | {
        total: number; created_today: number; created_7d: number;
        with_imported_games: number; with_analysed_games: number;
        recent: { id: string; email: string | null; created_at: number; last_login_at: number | null;
                  imported: number; analysed: number; reviewed: number }[];
    };
    imports: 'not_tracked' | { chesscom: SourceCounts; lichess: SourceCounts; manual: SourceCounts;
                               external_search_failures: number; duplicates_refused: number };
    reviews: 'not_tracked' | Record<string, Metric>;
    corrections: 'not_tracked' | Record<string, Metric>;
    failures: 'not_tracked' | Record<string, Metric | Record<string, number>>;
    latency: 'not_tracked' | Record<string, Latency>;
    recent_failures: { event: string; at: number; mode: string | null; operation: string | null;
                       category: string | null; code: string | null }[];
    funnel: 'not_tracked' | { step: string; count: number }[];
    recent_accounts: RecentAccount[];
    import_health: 'not_tracked' | (Record<string, Metric | Record<string, number>> & {
        recent_events: { at: number; event: string; source: string | null; category: string | null; games: number | null }[];
    });
    provider_health: 'not_tracked' | Record<string, Metric | Latency | Record<string, number>>;
    review_health: 'not_tracked' | Record<string, Metric>;
    profile_evidence: 'not_tracked' | {
        label: string; total_rows: number; detector_findings: number; correction_evidence: number;
        by_source: Record<string, Metric>; top_themes: { theme: string; count: number }[];
    };
}

interface RecentAccount {
    id: string; email: string | null; created_at: number; last_active_at: number | null;
    chesscom_games: number; lichess_games: number; manual_games: number;
    reviews_completed: number; corrections_generated: number;
    practice_started: number; practice_completed: number; failures: number;
}

const NOT_TRACKED = 'not tracked';

function fmt(v: unknown): string {
    if (v === null || v === undefined) return '—';
    if (v === 'not_tracked') return NOT_TRACKED;
    if (typeof v === 'object' && 'samples' in (v as object)) {
        const l = v as Latency;
        return l.samples ? `avg ${l.avg_ms} ms · p95 ${l.p95_ms} ms · n=${l.samples}` : 'no samples yet';
    }
    if (typeof v === 'object') {
        const entries = Object.entries(v as Record<string, number>);
        return entries.length ? entries.map(([k, n]) => `${k} ${n}`).join(', ') : '0';
    }
    return String(v);
}

const label = (key: string) => key.replace(/_/g, ' ');
const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : '—');

function Rows({ data }: { data: 'not_tracked' | Record<string, unknown> }) {
    if (data === 'not_tracked') return <p className="settings-card-sub">{NOT_TRACKED}</p>;
    return (
        <>
            {Object.entries(data).map(([k, v]) => (
                <div className="settings-row" key={k}>
                    <span className="settings-row-label">{label(k)}</span>
                    <span className="settings-row-value">{fmt(v)}</span>
                </div>
            ))}
        </>
    );
}

function Section({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
    return (
        <section className="settings-card" data-admin-section={title}>
            <h2 className="settings-card-title">{title}</h2>
            {sub && <p className="settings-card-sub">{sub}</p>}
            {children}
        </section>
    );
}

export function Admin() {
    const [overview, setOverview] = useState<Overview | null>(null);
    const [blocked, setBlocked] = useState<{ status?: number; message: string } | null>(null);

    useEffect(() => {
        apiJson<{ overview: Overview }>('/api/admin/overview')
            .then((r) => setOverview(r.overview))
            .catch((err: Error & { status?: number }) =>
                setBlocked({ status: err.status, message: err.message }));
    }, []);

    if (blocked) {
        return (
            <div className="settings-page" data-admin-state="blocked">
                <div className="settings-shell">
                    <div className="settings-head">
                        <h1 className="settings-h1">Admin</h1>
                        <Link className="acct-btn acct-btn-quiet" to="/">Back to the board</Link>
                    </div>
                    <section className="settings-card">
                        <h2 className="settings-card-title">
                            {blocked.status === 401 ? 'Sign in required' : 'Not available'}
                        </h2>
                        <p className="settings-card-sub">{blocked.message}</p>
                        {blocked.status === 401 && <Link className="auth-link" to="/signin">Sign in</Link>}
                    </section>
                </div>
            </div>
        );
    }

    if (!overview) {
        return (
            <div className="settings-page" data-admin-state="loading">
                <div className="settings-shell"><p className="settings-saved">Loading the overview…</p></div>
            </div>
        );
    }

    const a = overview.accounts;
    const tiles: [string, unknown][] = [
        ['Accounts', a === 'not_tracked' ? a : a.total],
        ['Imported games', overview.imports === 'not_tracked' ? overview.imports : overview.imports.chesscom.games + overview.imports.lichess.games + overview.imports.manual.games],
        ['Reviews completed', overview.reviews === 'not_tracked' ? overview.reviews : overview.reviews.completed],
        ['Corrections generated', overview.corrections === 'not_tracked' ? overview.corrections : overview.corrections.cards_generated],
    ];

    return (
        <div className="settings-page" data-admin-state="overview">
            <div className="settings-shell admin-shell">
                <div className="settings-head">
                    <h1 className="settings-h1">Admin</h1>
                    <Link className="acct-btn acct-btn-quiet" to="/settings">Back to settings</Link>
                </div>
                <p className="settings-card-sub">
                    Beta overview, read-only. Generated {when(overview.generated_at)}
                    {!overview.database && ' — no database configured, nothing to count.'}
                </p>

                <div className="admin-tiles">
                    {tiles.map(([name, value]) => (
                        <div className="settings-card admin-tile" key={name}>
                            <span className="admin-tile-value">{fmt(value)}</span>
                            <span className="admin-tile-label">{name}</span>
                        </div>
                    ))}
                </div>

                <Section title="Beta funnel" sub="How far people get. Each step counts events, not people, except the first two.">
                    {overview.funnel === 'not_tracked' ? <p className="settings-card-sub">{NOT_TRACKED}</p> : (
                        <div className="admin-funnel">
                            {overview.funnel.map((f) => (
                                <div className="admin-funnel-step" key={f.step}>
                                    <span className="admin-tile-value">{f.count}</span>
                                    <span className="admin-tile-label">{label(f.step)}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </Section>

                <Section title="Recent accounts" sub="Newest first. Counts only; nothing here can be acted on.">
                    {a !== 'not_tracked' && (
                        <Rows data={{ total: a.total, created_today: a.created_today, created_last_7_days: a.created_7d,
                                      with_imported_games: a.with_imported_games, with_analysed_games: a.with_analysed_games }} />
                    )}
                    <div className="admin-table-wrap">
                        <table className="admin-table" data-testid="recent-accounts">
                            <thead><tr>
                                <th>created</th><th>id</th><th>email</th><th>last active</th>
                                <th>chess.com</th><th>lichess</th><th>manual</th>
                                <th>reviews</th><th>cards</th><th>practice</th><th>failures</th>
                            </tr></thead>
                            <tbody>
                                {overview.recent_accounts.map((u) => (
                                    <tr key={u.id}>
                                        <td>{when(u.created_at)}</td><td>{u.id}</td><td>{u.email ?? '—'}</td>
                                        <td>{when(u.last_active_at)}</td>
                                        <td>{u.chesscom_games}</td><td>{u.lichess_games}</td><td>{u.manual_games}</td>
                                        <td>{u.reviews_completed}</td><td>{u.corrections_generated}</td>
                                        <td>{u.practice_started} / {u.practice_completed}</td>
                                        <td>{u.failures}</td>
                                    </tr>
                                ))}
                                {overview.recent_accounts.length === 0 && <tr><td colSpan={11}>No accounts yet.</td></tr>}
                            </tbody>
                        </table>
                    </div>
                </Section>

                <Section title="Import health" sub="Chess.com and Lichess searches and what came of them.">
                    {overview.import_health === 'not_tracked' ? <p className="settings-card-sub">{NOT_TRACKED}</p> : (
                        <>
                            <Rows data={Object.fromEntries(Object.entries(overview.import_health)
                                .filter(([k]) => k !== 'recent_events' && k !== 'error_categories'))} />
                            <h3 className="admin-sub">Latest import events</h3>
                            {overview.import_health.recent_events.length === 0 ? <p className="settings-card-sub">None yet.</p> : (
                                <div className="admin-table-wrap">
                                    <table className="admin-table">
                                        <thead><tr><th>when</th><th>event</th><th>source</th><th>category</th><th>games</th></tr></thead>
                                        <tbody>
                                            {overview.import_health.recent_events.map((e, i) => (
                                                <tr key={i}>
                                                    <td>{when(e.at)}</td><td>{e.event}</td><td>{e.source ?? '—'}</td>
                                                    <td>{e.category ?? '—'}</td><td>{e.games ?? '—'}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </>
                    )}
                </Section>

                <Section title="AI / provider health" sub="Gemini on the move, diagnosis and chat paths; Stockfish when it falls back.">
                    <Rows data={overview.provider_health} />
                </Section>

                <Section title="Review / analysis health" sub="The whole-game scan, by where the game came from.">
                    <Rows data={overview.review_health} />
                </Section>

                <Section title="Improvement profile evidence" sub="Observed evidence only - single findings are not yet a pattern. Deterministic detector findings are kept apart from model-produced correction evidence.">
                    {overview.profile_evidence === 'not_tracked' ? <p className="settings-card-sub">{NOT_TRACKED}</p> : (
                        <>
                            <Rows data={{ total_rows: overview.profile_evidence.total_rows,
                                          detector_findings: overview.profile_evidence.detector_findings,
                                          correction_origin_evidence: overview.profile_evidence.correction_evidence,
                                          ...Object.fromEntries(Object.entries(overview.profile_evidence.by_source).map(([k, v]) => [`from_${k}`, v])) }} />
                            <h3 className="admin-sub">Most observed themes</h3>
                            {overview.profile_evidence.top_themes.length === 0 ? <p className="settings-card-sub">No evidence yet.</p> : (
                                <Rows data={Object.fromEntries(overview.profile_evidence.top_themes.map((t) => [t.theme, t.count]))} />
                            )}
                        </>
                    )}
                </Section>

                <Section title="Corrections and learning loop">
                    <Rows data={overview.corrections} />
                </Section>

                <Section title="Failures">
                    <Rows data={overview.failures} />
                </Section>

                <Section title="Recent failures">
                    {overview.recent_failures.length === 0 ? <p className="settings-card-sub">None recorded.</p> : (
                        <div className="admin-table-wrap">
                            <table className="admin-table">
                                <thead><tr><th>when</th><th>event</th><th>mode</th><th>operation</th><th>category</th><th>code</th></tr></thead>
                                <tbody>
                                    {overview.recent_failures.map((f, i) => (
                                        <tr key={i}>
                                            <td>{when(f.at)}</td><td>{f.event}</td><td>{f.mode ?? '—'}</td>
                                            <td>{f.operation ?? '—'}</td><td>{f.category ?? '—'}</td><td>{f.code ?? '—'}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </Section>
                <SiteFooter />
            </div>
        </div>
    );
}
