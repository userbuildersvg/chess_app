import { useEffect, useState } from 'react';
import { authService, type AuthConfig } from '../services/authService';

/** Read the deployment's account and email capabilities once. */
export function useAuthConfig(): AuthConfig | null {
    const [config, setConfig] = useState<AuthConfig | null>(null);
    useEffect(() => {
        authService.config()
            .then(setConfig)
            .catch(() => setConfig({
                accounts_enabled: false,
                guest_mode: true,
                google: false,
                unavailable_message: null,
                email_available: false,
            }));
    }, []);
    return config;
}
