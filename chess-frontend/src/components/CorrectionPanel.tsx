import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import type { Square } from 'chess.js';
import { EmptyState } from './EmptyState';
import { learningService } from '../services/learningService';
import type {
    Correction,
    CorrectionEvidence,
    LearningReference,
    PracticeStart,
    RetestPosition,
} from '../types/learning';
import { getCustomPieces } from '../pieceThemes';
import type { PieceThemeName } from '../pieceThemes';
import './CorrectionPanel.css';

/**
 * The learning loop, as one continuous thing.
 *
 * Post-Mortem's other three tabs answer questions about the game. This one is
 * a sequence with an order that matters, and the order is the product:
 *
 *   what were you trying to do?  ->  here is what you missed  ->  now play
 *   the better move yourself  ->  here is a fresh position testing the same
 *   idea  ->  saved
 *
 * Intent comes FIRST, before the diagnosis exists. That is not a nicety: a
 * diagnosis written against what the player was actually attempting is a
 * different thing from one written against the centipawn drop, and asking
 * afterwards would only be collecting agreement with an answer already given.
 *
 * TWO BOARDS, ON PURPOSE
 * ----------------------
 * "Try the better move" happens on the REAL board next door, through
 * Post-Mortem's existing branch flow - the same legal-move handling, the same
 * drag and click, the same move tree. Nothing about that is reimplemented
 * here; this panel only asks for it and notices when it happened.
 *
 * The re-test is a different position from a different game, and it gets its
 * own small board inside this panel. That is deliberate. Painting someone
 * else's position onto the board that has been showing *your game* is exactly
 * the confusion Post-Mortem works hardest to avoid - the mode already spends
 * a coloured frame and a "What if:" label keeping a branch distinct from the
 * real game, and a practice position is further from the real game than a
 * branch is.
 */

type Phase = 'intent' | 'diagnosis' | 'practice';

interface CorrectionPanelProps {
    /** The open review, or null on the empty canvas. */
    gameId: string | null;
    /** The node the board is currently on. */
    nodeId: string | null;
    /** The move that produced it, for naming the decision. Null at the start. */
    moveLabel: string | null;
    /** The move on the board was the opponent's. The question is still
     *  asked - "what were you expecting" is a real decision - but the panel
     *  says whose move it is, so nobody is asked to explain a move they did
     *  not make without knowing it. */
    opponentMove?: boolean;
    /** False on the game's starting position - there is no decision to diagnose. */
    canDiagnose: boolean;
    /** Whether the board is on the game rather than in a what-if. */
    onMainline: boolean;
    pieceTheme: PieceThemeName;
    /** Turn on Post-Mortem's own explore mode, so the better move can be played. */
    onRequestExplore: () => void | Promise<void>;
    /** The last move the player branched with, so we can notice they tried it. */
    lastBranchUci: string | null;
}

const CONFIDENCE_WORDS: [number, string][] = [
    [0.75, 'Confident'],
    [0.5, 'Fairly confident'],
    [0.0, 'Tentative'],
];

/** Why practice could not be made, in the student's terms - the codes are the server's. */
function practiceReasonText(reason: string | null): string {
    switch (reason) {
        case 'missing_snapshot':
            return 'This review did not preserve enough position evidence to build one.';
        case 'no_single_best_answer':
            return 'The engine did not find one clear fresh decision that tests the same idea.';
        case 'no_verified_position':
            return 'The position could not be verified as a clean single-answer test.';
        default:
            return reason ?? 'The current review did not preserve enough position evidence, or the engine did not find one clear fresh decision that tests the same idea.';
    }
}

function confidenceWord(value: number): string {
    return (CONFIDENCE_WORDS.find(([floor]) => value >= floor) ?? [0, 'Tentative'])[1];
}

function evalText(ev: { score: number | null; mate_in: number | null } | null): string {
    if (!ev) return 'unknown';
    if (ev.mate_in !== null) {
        if (ev.mate_in === 0) return 'checkmate';
        return `mate in ${Math.abs(ev.mate_in)} for ${ev.mate_in > 0 ? 'White' : 'Black'}`;
    }
    if (ev.score === null) return 'unknown';
    return `${ev.score >= 0 ? '+' : ''}${(ev.score / 100).toFixed(2)}`;
}

/** The evidence behind a card, rendered as facts with their provenance. */
function EvidenceList({ evidence }: { evidence: CorrectionEvidence[] }) {
    return (
        <ul className="corr-evidence-list">
            {evidence.map((item, index) => (
                <li key={`${item.node_id}-${index}`} className="corr-evidence-item">
                    <span className="corr-evidence-move">
                        {item.ply ? `${Math.ceil(item.ply / 2)}${item.color === 'white' ? '.' : '...'} ` : ''}
                        {item.san ?? 'a move'}
                    </span>
                    <span className="corr-evidence-detail">
                        {[
                            item.source_name,
                            item.best_san ? `engine preferred ${item.best_san}` : null,
                            `${evalText(item.eval_before)} → ${evalText(item.eval_after)}`,
                            item.cpl !== null && item.cpl > 0 ? `${item.cpl} centipawns` : null,
                            item.depth !== null ? `depth ${item.depth}` : null,
                        ].filter(Boolean).join(' · ')}
                    </span>
                    <span className="corr-evidence-intent">You said: “{item.intent}”</span>
                </li>
            ))}
        </ul>
    );
}

/** The re-test: one position, no numbers, no answer until it is answered. */
function RetestBoard({
    position,
    pieceTheme,
    disabled,
    onMove,
}: {
    position: RetestPosition;
    pieceTheme: PieceThemeName;
    disabled: boolean;
    onMove: (uci: string) => void;
}) {
    const [selected, setSelected] = useState<Square | null>(null);
    const customPieces = useMemo(() => getCustomPieces(pieceTheme), [pieceTheme]);

    // chess.js purely to know which squares a piece may go to, exactly as the
    // other three boards do. It is the same library the backend's legality
    // will agree with, and the backend re-checks the move anyway - this is for
    // the hints, not for the verdict.
    const board = useMemo(() => {
        try {
            return new Chess(position.fen);
        } catch {
            return null;
        }
    }, [position.fen]);

    const targets = useMemo(() => {
        const map = new Map<string, Set<string>>();
        if (!board) return map;
        for (const move of board.moves({ verbose: true })) {
            const set = map.get(move.from) ?? new Set<string>();
            set.add(move.to);
            map.set(move.from, set);
        }
        return map;
    }, [board]);

    useEffect(() => {
        setSelected(null);
    }, [position.fen]);

    const play = useCallback((from: string, to: string) => {
        // Auto-queen, matching every other board in this app - trap 13.
        const promotes = board?.get(from as Square)?.type === 'p'
            && (to[1] === '8' || to[1] === '1');
        onMove(`${from}${to}${promotes ? 'q' : ''}`);
    }, [board, onMove]);

    const squareStyles = useMemo(() => {
        const styles: Record<string, React.CSSProperties> = {};
        if (disabled || !selected) return styles;
        styles[selected] = { backgroundColor: 'var(--sq-selected)' };
        for (const target of targets.get(selected) ?? []) {
            styles[target] = {
                backgroundColor: board?.get(target as Square)
                    ? 'var(--sq-capture)'
                    : 'var(--sq-legal)',
            };
        }
        return styles;
    }, [selected, targets, board, disabled]);

    if (!board) {
        // A position that will not parse is a broken exercise, and the brief
        // is explicit: do not show a fake or invalid one.
        return (
            <p className="corr-note is-warn">
                This practice position could not be loaded, so it is not being shown.
            </p>
        );
    }

    return (
        <div className="corr-retest-board">
            <Chessboard
                position={position.fen}
                // The practice board has to fit under its heading inside the
                // panel, which is capped to the viewport: 300px at laptop
                // heights, smaller on a short screen rather than clipped.
                boardWidth={Math.max(220, Math.min(300, window.innerHeight - 480))}
                boardOrientation={board.turn() === 'w' ? 'white' : 'black'}
                arePiecesDraggable={!disabled}
                isDraggablePiece={({ sourceSquare }) => targets.has(sourceSquare)}
                onPieceDrop={(from, to) => {
                    setSelected(null);
                    if (disabled || !targets.get(from)?.has(to)) return false;
                    play(from, to);
                    return true;
                }}
                onSquareClick={(square: Square) => {
                    if (disabled) return;
                    if (selected && targets.get(selected)?.has(square)) {
                        setSelected(null);
                        play(selected, square);
                        return;
                    }
                    setSelected(targets.has(square) ? square : null);
                }}
                autoPromoteToQueen
                customSquareStyles={squareStyles}
                animationDuration={150}
                customPieces={customPieces}
                customDarkSquareStyle={{ backgroundColor: 'var(--board-dark)' }}
                customLightSquareStyle={{ backgroundColor: 'var(--board-light)' }}
            />
        </div>
    );
}

// A diagnosis that was in flight when the panel was left (another tab, a
// tab switch unmounts it). Remembered so the form does not come back blank as
// if nothing had been asked. One at a time: only one panel is ever mounted.
let setAsideOnLeave: { gameId: string; label: string } | null = null;
const setAsideNotice = (label: string) =>
    `You moved on while the correction for ${label} was being prepared, so it was set aside. Step back to that move to ask again.`;

export function CorrectionPanel({
    gameId,
    nodeId,
    moveLabel,
    opponentMove = false,
    canDiagnose,
    onMainline,
    pieceTheme,
    onRequestExplore,
    lastBranchUci,
}: CorrectionPanelProps) {
    const [reference, setReference] = useState<LearningReference | null>(null);
    const [phase, setPhase] = useState<Phase>('intent');
    const [preset, setPreset] = useState<string | null>(null);
    const [freeText, setFreeText] = useState('');
    const [busy, setBusy] = useState(false);
    const [activity, setActivity] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const [card, setCard] = useState<Correction | null>(null);
    const [source, setSource] = useState<'coach' | 'engine'>('coach');
    const [recurred, setRecurred] = useState(false);
    const [bestSan, setBestSan] = useState<string | null>(null);
    const [bestUci, setBestUci] = useState<string | null>(null);
    const [triedIt, setTriedIt] = useState(false);
    const [showEvidence, setShowEvidence] = useState(false);

    const [practice, setPractice] = useState<PracticeStart | null>(null);
    const [hint, setHint] = useState<string | null>(null);
    const [hintsUsed, setHintsUsed] = useState(0);
    const [result, setResult] = useState<
        { passed: boolean; bestSan: string; playedSan: string } | null
    >(null);
    const startedAt = useRef<number | null>(null);
    // The exercise is below a card that can run to a dozen lines, so it is
    // scrolled to rather than left for the player to find.
    const practiceRef = useRef<HTMLDivElement | null>(null);
    // The guidance line and the card are scrolled to when they appear: the
    // panel scrolls on its own, and the button that was just clicked sits at
    // the bottom of it, so whatever answers the click must come into view.
    const noteRef = useRef<HTMLParagraphElement | null>(null);
    const cardRef = useRef<HTMLDivElement | null>(null);
    // Scrolled AFTER the practice step has rendered - a frame scheduled from
    // the click ran before React had drawn it, so the panel stayed where the
    // button was and the board sat below the fold about one run in three.
    // Twice: the practice board measures itself after its first paint, so
    // the step is short when this first runs and there is nothing to scroll
    // yet; the second pass, once the board has its height, is the one that
    // lands.
    useEffect(() => {
        if (phase !== 'practice' || !practice) return;
        const go = () => practiceRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
        go();
        const t = window.setTimeout(go, 350);
        return () => window.clearTimeout(t);
    }, [phase, practice]);
    const flowRef = useRef<{
        active: boolean;
        completed: boolean;
        gameId: string | null;
        nodeId: string | null;
        correctionId: string | null;
    }>({ active: false, completed: false, gameId: null, nodeId: null, correctionId: null });
    // Starting an alternative deliberately navigates back one ply. Preserve
    // the diagnosis across that expected node change and the branch that
    // follows; it is the same correction cycle, not a newly selected move.
    const expectingAlternativePosition = useRef(false);
    const [diagnosedMoveLabel, setDiagnosedMoveLabel] = useState<string | null>(null);
    // The diagnosis request in flight, by the node it was asked about. Moving
    // to another decision while it runs sets it aside: the answer would be
    // about a move that is no longer on the board, so it is dropped rather
    // than shown under the wrong move.
    const inFlightRef = useRef<{ nodeId: string; label: string } | null>(null);
    const selectedEventRef = useRef<string | null>(null);
    const recordedAlternativeRef = useRef<string | null>(null);

    const [others, setOthers] = useState<Correction[]>([]);

    useEffect(() => {
        let live = true;
        void learningService.reference()
            .then(r => { if (live) setReference(r); })
            .catch(() => { /* the panel still works; only the presets are lost */ });
        return () => { live = false; };
    }, []);

    const refreshOthers = useCallback(() => {
        void learningService.corrections()
            .then(r => setOthers(r.corrections))
            .catch(() => {});
    }, []);

    useEffect(refreshOthers, [refreshOthers]);

    useEffect(() => {
        const selection = gameId && nodeId ? `${gameId}:${nodeId}` : null;
        if (gameId && nodeId && canDiagnose && onMainline
                && !flowRef.current.active && selectedEventRef.current !== selection) {
            selectedEventRef.current = selection;
            learningService.event('key_decision_selected', {
                game_id: gameId,
                node_id: nodeId,
            });
        }
    }, [gameId, nodeId, canDiagnose, onMainline]);

    // Leaving the Correct tab or selecting another move is the observable
    // abandonment boundary. Merely viewing a decision is not abandonment;
    // the flow becomes active only after intent is submitted.
    useEffect(() => () => {
        const pending = inFlightRef.current;
        if (pending && gameId) {
            setAsideOnLeave = { gameId, label: pending.label };
        }
        const flow = flowRef.current;
        if (flow.active && !flow.completed) {
            learningService.event('correction_flow_abandoned', {
                game_id: flow.gameId ?? undefined,
                node_id: flow.nodeId ?? undefined,
                correction_id: flow.correctionId ?? undefined,
                operation: 'left_correction_panel',
                completed: false,
            });
            flow.active = false;
        }
    }, [gameId]);

    // Moving the board to a different decision abandons the one in progress.
    // Keeping a card on screen for a move you are no longer looking at is the
    // fastest way to make someone believe a diagnosis is about the wrong move.
    const lastSelectionRef = useRef<string | null>(null);
    useEffect(() => {
        if (flowRef.current.active
                && (!onMainline || expectingAlternativePosition.current)) {
            expectingAlternativePosition.current = false;
            return;
        }
        // Same selection as last time (StrictMode's second pass): nothing to
        // reset, and the set-aside notice below must survive it.
        const selection = `${nodeId}:${onMainline}`;
        if (lastSelectionRef.current === selection) return;
        lastSelectionRef.current = selection;
        const flow = flowRef.current;
        if (flow.active && !flow.completed) {
            learningService.event('correction_flow_abandoned', {
                game_id: flow.gameId ?? undefined,
                node_id: flow.nodeId ?? undefined,
                correction_id: flow.correctionId ?? undefined,
                operation: 'selected_another_decision',
                completed: false,
            });
            flow.active = false;
        }
        setPhase('intent');
        setCard(null);
        setDiagnosedMoveLabel(null);
        setPractice(null);
        setResult(null);
        setHint(null);
        setHintsUsed(0);
        setTriedIt(false);
        recordedAlternativeRef.current = null;
        setShowEvidence(false);
        setError(null);
        setActivity(null);
        const pending = inFlightRef.current;
        if (pending && pending.nodeId !== nodeId) {
            inFlightRef.current = null;
            setBusy(false);
            setActivity(setAsideNotice(pending.label));
        } else if (setAsideOnLeave?.gameId === gameId) {
            setActivity(setAsideNotice(setAsideOnLeave.label));
            setAsideOnLeave = null;
        }
    }, [gameId, nodeId, onMainline]);

    // Noticing that the player actually played the engine's move on the real
    // board. The branch itself went through Post-Mortem; this only records it.
    useEffect(() => {
        if (!card || !lastBranchUci || !bestUci) return;
        if (lastBranchUci !== bestUci) return;
        const signature = `${card.id}:${lastBranchUci}`;
        if (recordedAlternativeRef.current === signature) return;
        recordedAlternativeRef.current = signature;
        setTriedIt(true);
        void learningService.branchTried(card.id, lastBranchUci).catch(() => {});
    }, [lastBranchUci, bestUci, card]);

    useEffect(() => {
        if (error) noteRef.current?.scrollIntoView({ block: 'nearest' });
    }, [error]);
    useEffect(() => {
        if (phase === 'diagnosis' && card) cardRef.current?.scrollIntoView({ block: 'start' });
    }, [phase, card]);

    const submitIntent = useCallback(async () => {
        if (!gameId || !nodeId) return;
        const intent = freeText.trim()
            || reference?.intent_presets.find(p => p.id === preset)?.label
            || '';
        if (!intent) {
            setError('Say what you were going for - even “I wasn’t sure” is an answer.');
            return;
        }
        setBusy(true);
        setActivity('Preparing correction… Checking the engine line and asking the coach.');
        setError(null);
        const requestStarted = performance.now();
        flowRef.current = {
            active: true,
            completed: false,
            gameId,
            nodeId,
            correctionId: null,
        };
        const request = { nodeId, label: moveLabel ?? 'this move' };
        inFlightRef.current = request;
        // Set aside by the reset effect above, or superseded by a newer ask.
        const stale = () => inFlightRef.current !== request;
        try {
            const out = await learningService.diagnose(gameId, nodeId, intent, preset);
            if (stale()) return;
            setCard(out.correction);
            setDiagnosedMoveLabel(moveLabel);
            setSource(out.diagnosis_source);
            setRecurred(out.recurred);
            setBestSan(out.best_san);
            setBestUci(out.best_move);
            setPhase('diagnosis');
            flowRef.current.correctionId = out.correction.id;
            learningService.event('correction_viewed', {
                game_id: gameId,
                node_id: nodeId,
                correction_id: out.correction.id,
                theme: out.correction.theme,
                duration_ms: Math.round(performance.now() - requestStarted),
                completed: true,
            });
            learningService.event('explanation_rendered', {
                game_id: gameId,
                node_id: nodeId,
                correction_id: out.correction.id,
                operation: 'correction_card',
                duration_ms: Math.round(performance.now() - requestStarted),
                completed: true,
            });
            refreshOthers();
        } catch (exc) {
            if (stale()) return;
            setError(exc instanceof Error ? exc.message : 'The coach could not answer just now.');
            learningService.event('correction_flow_error', {
                game_id: gameId,
                node_id: nodeId,
                operation: 'correction_generation',
                duration_ms: Math.round(performance.now() - requestStarted),
                error_category: 'request_failed',
                completed: false,
            });
        } finally {
            // A set-aside request leaves the new move's form and notice alone.
            if (!stale()) {
                inFlightRef.current = null;
                setBusy(false);
                setActivity(null);
            }
        }
    }, [gameId, nodeId, moveLabel, freeText, preset, reference, refreshOthers]);

    const beginAlternative = useCallback(async () => {
        expectingAlternativePosition.current = true;
        setBusy(true);
        setActivity('Returning to the position before this decision…');
        setError(null);
        try {
            await onRequestExplore();
        } catch (exc) {
            expectingAlternativePosition.current = false;
            setError(exc instanceof Error ? exc.message : 'That position could not be opened.');
        } finally {
            setBusy(false);
            setActivity(null);
        }
    }, [onRequestExplore]);

    const respond = useCallback(async (status: 'accepted' | 'rejected') => {
        if (!card) return;
        try {
            const out = await learningService.setStatus(card.id, status);
            setCard(out.correction);
        } catch {
            setError('That did not save.');
        }
    }, [card]);

    const beginPractice = useCallback(async () => {
        if (!card) return;
        setBusy(true);
        setActivity('Preparing fresh practice… Finding a verified position with the same idea.');
        setError(null);
        setResult(null);
        setHint(null);
        setHintsUsed(0);
        try {
            const out = await learningService.startPractice(card.id);
            setPractice(out);
            setPhase('practice');
            startedAt.current = Date.now();
        } catch (exc) {
            setError(exc instanceof Error ? exc.message : 'That position could not be loaded.');
        } finally {
            setBusy(false);
            setActivity(null);
        }
    }, [card]);

    const answer = useCallback(async (uci: string) => {
        if (!card) return;
        setBusy(true);
        setActivity('Checking your practice move…');
        setError(null);
        try {
            const elapsed = startedAt.current ? Date.now() - startedAt.current : null;
            const out = await learningService.attempt(card.id, uci, hintsUsed, elapsed);
            setResult({ passed: out.passed, bestSan: out.best_san, playedSan: out.played_san });
            setCard(out.correction);
            if (out.passed) {
                flowRef.current.completed = true;
            }
            refreshOthers();
        } catch (exc) {
            // A refused move must not look like a failed attempt - the player
            // has not answered yet, so nothing is recorded and nothing is said
            // about their understanding.
            setError(exc instanceof Error ? exc.message : 'That move was not accepted.');
        } finally {
            setBusy(false);
            setActivity(null);
        }
    }, [card, hintsUsed, refreshOthers]);

    const askHint = useCallback(async () => {
        if (!card) return;
        setBusy(true);
        setActivity('Preparing a hint…');
        try {
            const out = await learningService.hint(card.id);
            setHint(out.hint);
            setHintsUsed(n => n + 1);
        } catch {
            setError('No hint is available for this position.');
        } finally {
            setBusy(false);
            setActivity(null);
        }
    }, [card]);

    // --- what to render -----------------------------------------------------

    if (!gameId) {
        return (
            <EmptyState title="Bring a game first">
                Import a game in this mode, then open one of your decisions here to work on it.
            </EmptyState>
        );
    }

    if (!onMainline && !card) {
        return (
            <EmptyState title="You are in a what-if">
                Corrections are about the moves you actually played. Go back to the game and pick a
                decision from it.
            </EmptyState>
        );
    }

    if (!canDiagnose) {
        return (
            <EmptyState title="Pick a decision">
                Step to a move you played - or open one from <strong>Worth a second look</strong> in
                the Report tab - and work through it here.
            </EmptyState>
        );
    }

    return (
        <div className="corr-panel">
            <div className="corr-head">
                <span className="corr-eyebrow">Working on</span>
                <span className="corr-move">{diagnosedMoveLabel ?? moveLabel ?? 'this move'}</span>
            </div>

            <div
                className={`corr-progress ${activity ? 'is-active' : ''} ${!activity && phase !== 'intent' ? 'is-collapsed' : ''}`}
                role="status"
                aria-live="polite"
                aria-atomic="true"
            >
                {activity ?? '\u00a0'}
            </div>

            {error && phase !== 'intent' && <p className="corr-note is-warn" role="alert">{error}</p>}

            {phase === 'intent' && (
                <div className="corr-step">
                    {opponentMove && (
                        <p className="corr-note" data-testid="corr-opponent-note">
                            This was your opponent's move. To work on one of your own decisions,
                            step to a move you played - or answer for what you expected here.
                        </p>
                    )}
                    <h3 className="corr-question">
                        {opponentMove ? 'What were you expecting here?' : 'What were you trying to do?'}
                    </h3>
                    <p className="corr-sub" data-testid="corr-intent-next">
                        This helps the coach explain the mistake in a way that fits your thinking.
                    </p>
                    <div className="corr-presets">
                        {(reference?.intent_presets ?? []).map(option => (
                            <button
                                key={option.id}
                                type="button"
                                className={`corr-chip ${preset === option.id ? 'is-on' : ''}`}
                                aria-pressed={preset === option.id}
                                onClick={() => setPreset(preset === option.id ? null : option.id)}
                            >
                                {option.label}
                            </button>
                        ))}
                    </div>
                    <label className="corr-field">
                        <span className="corr-field-label">In your own words (optional)</span>
                        <textarea
                            className="corr-textarea"
                            value={freeText}
                            maxLength={300}
                            rows={3}
                            placeholder="e.g. I thought the knight was doing nothing on g8"
                            onChange={e => setFreeText(e.target.value)}
                        />
                    </label>
                    <p className="corr-hint" data-testid="corr-hint">
                        {preset || freeText.trim()
                            ? 'Then continue.'
                            : 'Not sure? That is useful too — choose “I wasn’t sure.”'}
                    </p>
                    {error && <p className="corr-note is-warn" role="alert" ref={noteRef}>{error}</p>}
                    <button
                        type="button"
                        className="action-btn corr-primary"
                        onClick={() => void submitIntent()}
                        // Enabled without an intention on purpose: a click then
                        // gets the "say what you were going for" line instead
                        // of a button that does nothing and explains nothing.
                        disabled={busy}
                    >
                        {busy ? 'Preparing correction…' : 'Show me what I missed'}
                    </button>
                </div>
            )}

            {phase === 'diagnosis' && card && (
                <div className="corr-step">
                    <div className="corr-card" ref={cardRef}>
                        <div className="corr-card-lede">
                            <h3 className="corr-card-title">Your saved lesson</h3>
                            <p className="corr-card-sub">A short lesson from one decision in your game.</p>
                        </div>
                        <div className="corr-card-head">
                            <span className="corr-theme">{card.theme_label}</span>
                            {recurred && card.occurrence_count > 1 && (
                                <span className="corr-recur" title="The same idea, filed under the same theme">
                                    Seen before · {card.occurrence_count}×
                                </span>
                            )}
                        </div>

                        {/* Where the lesson lives, first, because it decides whether
                            any of this outlasts the tab. One vocabulary for persistence
                            (CLAUDE.md §42): saved / saving / could not save / session. */}
                        <div className="corr-status" data-testid="corr-status">
                            <span className={`corr-chip-status ${busy && card.save_failed ? 'is-saving' : card.saved_to_account ? 'is-saved' : card.save_failed ? 'is-failed' : 'is-session'}`} data-testid="corr-saved-chip">
                                {busy && card.save_failed ? 'Saving…' : card.saved_to_account ? 'Saved — Zugzwang will remember this.' : card.save_failed ? 'Could not save — retry' : 'Kept for this session'}
                            </span>
                            {card.save_failed && !busy && (
                                <button type="button" className="corr-link" onClick={() => void submitIntent()} data-testid="corr-save-retry">
                                    Retry
                                </button>
                            )}
                        </div>
                        {!card.saved_to_account && (
                            <p className="corr-fineprint" data-testid="correction-storage-copy">
                                {card.save_failed
                                    ? <>Your account could not be reached, so this lesson is kept for this session only. Retry to save it.</>
                                    : <><a className="corr-link" href="/signup">Create an account</a> to keep this lesson in your improvement profile.</>}
                            </p>
                        )}

                        {source === 'engine' && (
                            <p className="corr-note is-warn">
                                The coach could not be reached, so this is the engine's reading only.
                            </p>
                        )}

                        {/* The lesson, in the order a person reads it: what they
                            meant, what the position needed, the stronger move, why
                            it mattered, the rule. Every field is the card's own. */}
                        {card.player_intent && (
                            <div className="corr-zone" data-testid="corr-zone-intent">
                                <span className="corr-zone-label">What you were trying to do</span>
                                <p className="corr-zone-text">{card.player_intent}</p>
                            </div>
                        )}
                        <div className="corr-zone" data-testid="corr-zone-mattered">
                            <span className="corr-zone-label">What the position needed</span>
                            <p className="corr-zone-text corr-missed">{card.missed_factor}</p>
                        </div>
                        {/* Only when the engine's move differs from the one played:
                            on a position already lost, the best move can be the
                            move that was played, and "Stronger move: Nxd7" under a
                            card about Nxd7 would be a lie by layout. */}
                        {bestSan && bestSan !== (diagnosedMoveLabel ?? moveLabel ?? '').split(' ').pop() && (
                            <div className="corr-zone corr-zone-move" data-testid="corr-zone-stronger">
                                <span className="corr-zone-label">Stronger move</span>
                                <p className="corr-zone-text corr-stronger">{bestSan}</p>
                            </div>
                        )}
                        <div className="corr-zone" data-testid="corr-zone-coach">
                            <span className="corr-zone-label">Why it mattered</span>
                            <p className="corr-diagnosis">{card.diagnosis}</p>
                            {card.uncertainty && (
                                <p className="corr-uncertainty"><strong>Caveat:</strong> {card.uncertainty}</p>
                            )}
                        </div>
                        <div className="corr-zone corr-zone-rule" data-testid="corr-zone-rule">
                            <span className="corr-zone-label">Next-time rule</span>
                            <p className="corr-zone-text corr-rule">{card.correction_rule}</p>
                        </div>

                        {/* The two things you do with a lesson. Practise leads when
                            there is a position to practise on; the board is always
                            there. */}
                        <div className="corr-card-actions">
                            {card.practice_available && (
                                <button
                                    type="button"
                                    className="action-btn corr-primary"
                                    onClick={() => void beginPractice()}
                                    disabled={busy}
                                    data-testid="corr-practice-available"
                                >
                                    {busy ? 'Preparing practice…' : 'Practise this idea'}
                                </button>
                            )}
                            {triedIt
                                ? <p className="corr-note is-good">You played it. That line is yours to explore.</p>
                                : (
                                    <button
                                        type="button"
                                        className="action-btn"
                                        onClick={() => void beginAlternative()}
                                        disabled={busy}
                                    >
                                        Try the better move on the board
                                    </button>
                                )}
                        </div>

                        {/* The engine's figures, small and secondary: they are how
                            we know, not the lesson. */}
                        {(() => {
                            const ev = card.evidence?.at(-1);
                            const evalText = (e: { score: number | null; mate_in: number | null } | null | undefined) =>
                                !e ? null : e.mate_in != null ? `mate in ${Math.abs(e.mate_in)}` : e.score != null ? `${e.score >= 0 ? '+' : ''}${(e.score / 100).toFixed(2)}` : null;
                            const before = evalText(ev?.eval_before), after = evalText(ev?.eval_after);
                            return (
                                <div className="corr-zone corr-zone-engine" data-testid="corr-zone-engine">
                                    <span className="corr-zone-label">How we know</span>
                                    <ul className="corr-engine-list">
                                        {ev?.san && before && <li>Before {ev.san}: <strong>{before}</strong>{after ? <> → after: <strong>{after}</strong></> : null}</li>}
                                        {ev?.best_san && <li>Engine preferred <strong>{ev.best_san}</strong></li>}
                                        {ev?.cpl != null && ev.cpl > 0 && <li>Centipawn loss: <strong>{ev.cpl}</strong></li>}
                                        <li>{ev?.depth ? `Stockfish depth ${ev.depth}` : 'Available engine search'} — close calls can change with a deeper search.</li>
                                    </ul>
                                </div>
                            );
                        })()}

                        <div className="corr-meta">
                            <span>{confidenceWord(card.confidence)}</span>
                            <button
                                type="button"
                                className="corr-link"
                                aria-expanded={showEvidence}
                                onClick={() => setShowEvidence(v => !v)}
                            >
                                {showEvidence ? 'Hide the evidence' : 'Why do you think this?'}
                            </button>
                        </div>

                        {showEvidence && card.evidence && <EvidenceList evidence={card.evidence} />}

                        {/* Feedback stays, quietly: the lesson is theirs to accept or
                            correct, and a rejection is the signal the coach learns from. */}
                        <div className="corr-respond">
                            <span className="corr-respond-label">Does this fit?</span>
                            <button
                                type="button"
                                className={`corr-chip ${card.status === 'accepted' ? 'is-on' : ''}`}
                                onClick={() => void respond('accepted')}
                            >
                                That makes sense
                            </button>
                            <button
                                type="button"
                                className={`corr-chip ${card.status === 'rejected' ? 'is-on' : ''}`}
                                onClick={() => void respond('rejected')}
                            >
                                That wasn't my plan
                            </button>
                        </div>
                    </div>

                    {phase === 'diagnosis' && !card.practice_available && (
                        <div className="corr-try corr-practice-none" data-testid="corr-practice-unavailable">
                            <p className="corr-sub">
                                <strong>We couldn't create a clean fresh test for this lesson yet.</strong>{' '}
                                {practiceReasonText(card.practice_unavailable_reason)}
                            </p>
                            <p className="corr-sub">
                                {card.saved_to_account
                                    ? <>The lesson is saved; you can revisit it from <a className="corr-link" href="/profile">My improvement</a>.</>
                                    : card.save_failed
                                        ? <>Retry the save above to keep this lesson on your account.</>
                                        : <>This lesson is kept for this session. <a className="corr-link" href="/signup">Create an account</a> to keep it and revisit it later.</>}
                            </p>
                            {card.saved_to_account && (
                                <div className="corr-practice-none-actions">
                                    <a className="action-btn" href="/profile">Open My improvement</a>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {phase === 'practice' && card && (
                <button
                    type="button"
                    className="corr-collapsed"
                    onClick={() => setPhase('diagnosis')}
                >
                    <span className="corr-collapsed-theme">Your saved lesson · {card.theme_label}</span>
                    <span className="corr-collapsed-back">Review the saved lesson</span>
                </button>
            )}

            {phase === 'practice' && practice && (
                <div className="corr-step" ref={practiceRef}>
                    {!practice.available ? (
                        <>
                            <h3 className="corr-question">Practice unavailable for this lesson</h3>
                            {/* Honest, and the reason is the interesting part:
                                a position is only used here when the engine
                                confirms one right answer. */}
                            <p className="corr-sub">{practice.reason}</p>
                            <button type="button" className="action-btn" onClick={() => setPhase('diagnosis')}>
                                Review the saved lesson
                            </button>
                        </>
                    ) : (
                        <>
                            <h3 className="corr-question" data-testid="corr-practice-next">
                                {card?.theme_label
                                    ? 'Your turn. Find the move that creates the stronger idea.'
                                    : 'Your turn. Find the move that would have improved this position.'}
                            </h3>
                            <p className="corr-sub corr-notyours">
                                {practice.position.from_your_game
                                    ? 'This position comes from the decision you just reviewed - a real position from your game.'
                                    : 'Same lesson as your saved card, on a different position - to see whether the idea transfers.'}
                            </p>
                            <RetestBoard
                                position={practice.position}
                                pieceTheme={pieceTheme}
                                disabled={busy || result !== null}
                                onMove={uci => void answer(uci)}
                            />
                            {!result && (
                                <div className="corr-practice-actions">
                                    <button type="button" className="corr-link" onClick={() => void askHint()} disabled={busy}>
                                        Give me a hint
                                    </button>
                                </div>
                            )}
                            {hint && !result && <p className="corr-note">{hint}</p>}
                            {/* Where the position came from and what it was checked
                                against: the server's own line, under the board where it
                                is provenance rather than the instruction. */}
                            <p className="corr-fineprint">
                                {practice.position.from_your_game ? 'Taken from your analyzed game. ' : ''}{practice.position.prompt}
                            </p>
                            {result && (
                                <>
                                    <p className={`corr-note ${result.passed ? 'is-good' : 'is-warn'}`} role="status">
                                        {result.passed
                                            ? <><strong>You found the idea.</strong> {result.bestSan} — the same move in a fresh position.</>
                                            : <><strong>Not quite.</strong> You played {result.playedSan}; the stronger move was {result.bestSan}. Look at what that move changes before you try again.</>}
                                    </p>
                                    {result.passed && card?.correction_rule && (
                                        <p className="corr-rule">{card.correction_rule}</p>
                                    )}
                                    <p className="corr-sub">
                                        Practice on this lesson: {card?.practice_summary.passed ?? 0} of{' '}
                                        {card?.practice_summary.attempted ?? 0}.
                                    </p>
                                    <div className="corr-practice-actions">
                                        <button type="button" className="action-btn corr-primary" onClick={() => void beginPractice()}>
                                            Try again
                                        </button>
                                        <button type="button" className="action-btn" onClick={() => setPhase('diagnosis')}>
                                            Review the saved lesson
                                        </button>
                                        <a className="action-btn" href="/profile">Open My improvement</a>
                                    </div>
                                </>
                            )}
                        </>
                    )}
                </div>
            )}

            {others.length > 0 && (
                <div className="corr-others">
                    <h3 className="corr-section-title">Your saved lessons</h3>
                    <ul className="corr-others-list">
                        {others.map(item => (
                            <li key={item.id} className="corr-others-item">
                                <span className="corr-others-theme">{item.theme_label}</span>
                                <span className="corr-others-meta">
                                    {item.occurrence_count > 1 ? `${item.occurrence_count}× · ` : ''}
                                    {item.practice_summary.attempted > 0
                                        ? `practice ${item.practice_summary.passed}/${item.practice_summary.attempted}`
                                        : 'not practised'}
                                </span>
                            </li>
                        ))}
                    </ul>
                    <p className="corr-fineprint">
                        {others.some(item => item.saved_to_account)
                            ? 'Saved to your improvement profile.'
                            : 'Kept for this session - these lessons are not saved to an account.'}
                    </p>
                </div>
            )}
        </div>
    );
}
