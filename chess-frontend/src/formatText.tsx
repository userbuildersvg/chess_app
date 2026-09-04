import React from 'react';

/**
 * Render the small amount of markdown Gemini actually emits.
 *
 * Its replies occasionally use `**bold**` for emphasis - most often on a move
 * or an opening name, "that's **Fool's Mate**", "the engine likes **1. e4**".
 * Left as plain text those asterisks show up literally, which reads as the
 * model leaking its own formatting into the answer.
 *
 * Deliberately not a markdown library. One formatting case does not justify a
 * dependency, and a full parser would start interpreting things this text is
 * not - a lone `*` in "1. e4 *!*", an underscore in a variable name. Anything
 * that is not a matched `**...**` pair falls through unchanged as plain text,
 * which is the failure mode you want: worst case the reader sees what the
 * model wrote.
 *
 * Newlines are honoured too, because the replies are paragraphs.
 *
 * Shared by the real game's chat and Learner Mode's. It lived in
 * ChessBoard.tsx and was applied only there, so the same reply rendered with
 * bold in one mode and with visible asterisks in the other - and once the
 * sandbox composer became the main way into Learner Mode, that was the more
 * visible of the two.
 */
export function renderFormattedText(text: string): React.ReactNode {
    const segments = text.split(/(\*\*[^*]+\*\*)/g);
    return segments.map((segment, i) => {
        const boldMatch = segment.match(/^\*\*([^*]+)\*\*$/);
        const content = boldMatch ? boldMatch[1] : segment;
        const lines = content.split('\n').map((line, j) => (
            <React.Fragment key={j}>
                {j > 0 && <br />}
                {line}
            </React.Fragment>
        ));
        return boldMatch
            ? <strong key={i}>{lines}</strong>
            : <React.Fragment key={i}>{lines}</React.Fragment>;
    });
}
