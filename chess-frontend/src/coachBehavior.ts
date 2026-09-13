import { readLocal, writeLocal } from './services/preferences';

export type CoachBehaviorSettings = {
    bluntness: number;
    creativity: number;
    preset: string;
};

export const DEFAULT_COACH_BEHAVIOR: CoachBehaviorSettings = {
    bluntness: 5,
    creativity: 5,
    preset: 'balanced',
};

export const COACH_PRESETS = [
    { id: 'balanced', label: 'Balanced Coach', bluntness: 5, creativity: 5 },
    { id: 'gentle', label: 'Gentle Explainer', bluntness: 1.5, creativity: 3 },
    { id: 'blunt', label: 'Blunt Tactician', bluntness: 9.5, creativity: 2 },
    { id: 'creative', label: 'Creative Mentor', bluntness: 3, creativity: 9 },
    { id: 'sharp-story', label: 'Sharp Story Coach', bluntness: 8.5, creativity: 8.5 },
    { id: 'minimal', label: 'Minimal Analyst', bluntness: 7, creativity: 1 },
] as const;

export type StyleBand = 'extreme_low' | 'moderate_low' | 'balanced' | 'moderate_high' | 'extreme_high';

// Mirrors coach_style.band() on the backend: 1-2, 3-4, 5-6, 7-8, 9-10 with
// the cut points halfway between, so the preview here and the prompt block
// there switch voice at the same coordinate.
export function styleBand(value: number): StyleBand {
    if (value < 2.5) return 'extreme_low';
    if (value < 4.5) return 'moderate_low';
    if (value <= 6.5) return 'balanced';
    if (value < 8.5) return 'moderate_high';
    return 'extreme_high';
}

export const BAND_LABELS: Record<StyleBand, string> = {
    extreme_low: 'very gentle',
    moderate_low: 'gentle',
    balanced: 'balanced',
    moderate_high: 'direct',
    extreme_high: 'blunt',
};

export const CREATIVITY_LABELS: Record<StyleBand, string> = {
    extreme_low: 'plain',
    moderate_low: 'mostly plain',
    balanced: 'natural',
    moderate_high: 'expressive',
    extreme_high: 'vivid',
};

// One position, one fact - "your e4 pawn is under pressure" - said 25 ways.
// Rows are directness bands, columns are creativity bands. Only the wording
// changes; the fact does not, which is the whole contract of the setting.
const PREVIEW: Record<StyleBand, Record<StyleBand, string>> = {
    extreme_low: {
        extreme_low: 'One thing to gently notice: your e4 pawn may need support.',
        moderate_low: 'One thing to notice: your e4 pawn may need a defender before your next plan.',
        balanced: 'Your idea makes sense; one thing worth a look is whether e4 has enough support.',
        moderate_high: 'Your plan is reasonable; the e4 pawn is quietly carrying a lot of weight, so it may be worth giving it a hand.',
        extreme_high: 'Your e4 pawn is starting to carry a lot of weight; make sure it has support before you chase another idea.',
    },
    moderate_low: {
        extreme_low: 'Your e4 pawn is under pressure. Consider defending it.',
        moderate_low: 'Your e4 pawn is under pressure now, so it is worth defending it before anything else.',
        balanced: 'I see the idea, but e4 is under pressure now and could use a defender first.',
        moderate_high: 'I see the idea, but e4 is the pawn taking the strain here; give it support before you look elsewhere.',
        extreme_high: 'I see the idea, but e4 has become the hinge of your position, and it is starting to creak; support it first.',
    },
    balanced: {
        extreme_low: 'Your e4 pawn is attacked. Check whether it is defended.',
        moderate_low: 'Your e4 pawn is attacked and not yet defended. Deal with that before your own plan.',
        balanced: 'Your e4 pawn is under pressure, and your next move should account for it before anything else.',
        moderate_high: 'Your e4 pawn is now the pressure point in the center; your next move has to answer that before your own plan.',
        extreme_high: 'Your e4 pawn is the loose thread in the center, and your next move has to hold it before you pull on anything else.',
    },
    moderate_high: {
        extreme_low: 'e4 is attacked and undefended. Defend it now.',
        moderate_low: 'e4 is attacked and undefended. Defend it now or you lose central material.',
        balanced: 'e4 is hanging. Defend it now, or the center starts to go.',
        moderate_high: 'e4 is hanging, and it is the pin holding your center together. Defend it now.',
        extreme_high: 'e4 is hanging, and it is the pin holding your center together; pull it and the whole middle sags.',
    },
    extreme_high: {
        extreme_low: 'Your e4 pawn is attacked. Defend it or lose central material.',
        moderate_low: 'Your e4 pawn is attacked and undefended. Defend it or lose central material.',
        balanced: 'Your e4 pawn is hanging. Defend it or lose the center.',
        moderate_high: 'Your e4 pawn is hanging, and it is the keystone of your center. Ignore it and the center falls.',
        extreme_high: 'Your e4 pawn is the loose bolt in the center. Ignore it, and the position starts coming apart.',
    },
};

export function coachPreview(settings: Pick<CoachBehaviorSettings, 'bluntness' | 'creativity'>): string {
    return PREVIEW[styleBand(settings.bluntness)][styleBand(settings.creativity)];
}

export function coachVoiceLabel(settings: Pick<CoachBehaviorSettings, 'bluntness' | 'creativity'>): string {
    return `${BAND_LABELS[styleBand(settings.bluntness)]} · ${CREATIVITY_LABELS[styleBand(settings.creativity)]}`;
}

const safeCoordinate = (raw: string | null): number | null => {
    if (raw === null) return null;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 0 && value <= 10
        ? Math.round(value * 10) / 10
        : null;
};

export function readCoachBehavior(): CoachBehaviorSettings {
    const bluntness = safeCoordinate(readLocal('chess-coach-bluntness'));
    const creativity = safeCoordinate(readLocal('chess-coach-creativity'));
    const preset = readLocal('chess-coach-style-preset');
    return {
        bluntness: bluntness ?? DEFAULT_COACH_BEHAVIOR.bluntness,
        creativity: creativity ?? DEFAULT_COACH_BEHAVIOR.creativity,
        preset: COACH_PRESETS.some(item => item.id === preset)
            ? preset!
            : preset === 'custom' ? 'custom' : DEFAULT_COACH_BEHAVIOR.preset,
    };
}

export function saveCoachBehavior(settings: CoachBehaviorSettings): void {
    writeLocal('chess-coach-bluntness', String(settings.bluntness));
    writeLocal('chess-coach-creativity', String(settings.creativity));
    writeLocal('chess-coach-style-preset', settings.preset);
}

export function coachBehaviorPayload() {
    const { bluntness, creativity } = readCoachBehavior();
    return { coach_style: { bluntness, creativity } };
}

export function coachStyleSummary(settings: CoachBehaviorSettings): string {
    const preset = COACH_PRESETS.find(item => item.id === settings.preset);
    return preset?.label ?? `${settings.bluntness.toFixed(1)} direct · ${settings.creativity.toFixed(1)} creative`;
}
