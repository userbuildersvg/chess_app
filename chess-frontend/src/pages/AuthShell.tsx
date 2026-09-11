import type { ReactNode } from 'react';
import { SiteFooter } from '../components/SiteFooter';

/** Shared shell for sign-in, sign-up, and password-reset surfaces. */
export function AuthShell({ title, sub, children }: {
    title: string;
    sub: string;
    children: ReactNode;
}) {
    return (
        <div className="auth-page">
            <div className="auth-card">
                <p className="auth-brand">Zugzwang</p>
                <h1 className="auth-title">{title}</h1>
                <p className="auth-sub">{sub}</p>
                {children}
            </div>
            <SiteFooter />
        </div>
    );
}
