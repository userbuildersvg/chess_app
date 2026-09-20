import { OPPONENT_PROFILES, profileLabel } from '../opponentProfiles';
import { useImprovementSnapshot } from '../hooks/useImprovementSnapshot';
import { LevelPicker } from './LevelPicker';

/**
 * The opponent level, as one compact control shared by Play and Learn.
 *
 * A trigger that says what you have ("Club — about 1500"), opening the seven
 * profiles with the Elo band and one line on each, the current one marked.
 * It replaced a native <select>: seven options with a blurb each did not
 * fit an <option>, so the blurb lived in a hover title nobody found.
 *
 * The eighth row, "Matched to me", is the profile-calibrated level. There is
 * no server calibration yet, so it is never selectable: it says what would
 * unlock it - an account, then ten analysed games - and once those exist it
 * says the calibration is still to come. Nothing here guesses a rating.
 */
export function OpponentLevelPicker({ value, onChange, disabled, ariaLabel = 'Opponent level' }: {
    value: string;
    onChange: (id: string) => void;
    disabled?: boolean;
    ariaLabel?: string;
}) {
    const snap = useImprovementSnapshot();
    const matched = !snap.loaded ? 'An opponent set to the level your analysed games imply.'
        : !snap.signedIn ? 'Build your improvement profile first.'
        : !snap.profile?.ready ? `Analyze more games to unlock profile-matched difficulty (${snap.profile?.analysed_games ?? 0} of ${snap.profile?.minimum_games ?? 10} so far).`
        : 'Your profile is ready; the level calibration behind this is still being built.';
    return (
        <LevelPicker
            value={value}
            current={profileLabel(value)}
            options={OPPONENT_PROFILES.map(p => ({
                id: p.id,
                label: p.label,
                tag: `about ${p.id === 'master' ? `${p.approxElo}+` : p.approxElo}`,
                blurb: p.blurb,
            }))}
            locked={[{ id: 'matched', label: 'Matched to me', blurb: matched, testId: 'level-matched' }]}
            onChange={onChange}
            disabled={disabled}
            ariaLabel={ariaLabel}
        />
    );
}
