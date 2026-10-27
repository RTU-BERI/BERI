import {createContext, useCallback, useContext, useEffect, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {authApi} from './api';
import type {LoginInput, Session} from './types';

type Auth = Session & {status: 'loading' | 'authenticated' | 'anonymous'; loadError: string | null; notice: string | null;
  refresh: () => Promise<void>; login: (input: LoginInput) => Promise<void>; logout: () => Promise<void>};
const Context = createContext<Auth | undefined>(undefined);
export function AuthProvider({children}: {children: ReactNode}) {
  const [session, setSession] = useState<Session>({authenticated: false, user: null});
  const [status, setStatus] = useState<Auth['status']>('loading');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const generation = useRef(0);
  const actionPending = useRef(false);
  const previousAuthenticated = useRef(false);
  const accept = useCallback((next: Session) => {
    if (previousAuthenticated.current && !next.authenticated) setNotice('Сессия завершилась. Войдите снова.');
    previousAuthenticated.current = next.authenticated;
    setSession(next); setStatus(next.authenticated ? 'authenticated' : 'anonymous'); setLoadError(null);
  }, []);
  const refresh = useCallback(async () => {
    if (actionPending.current) return;
    const id = ++generation.current;
    try {const next = await authApi.me(); if (id === generation.current) accept(next);}
    catch {if (id === generation.current) setLoadError('Не удалось проверить сессию. Повторите попытку.');}
  }, [accept]);
  useEffect(() => {
    void refresh();
    const onFocus = () => {void refresh();};
    window.addEventListener('focus', onFocus); window.addEventListener('beri:session-expired', onFocus);
    return () => {++generation.current; window.removeEventListener('focus', onFocus); window.removeEventListener('beri:session-expired', onFocus);};
  }, [refresh]);
  const login = async (input: LoginInput) => {
    actionPending.current = true;
    const id = ++generation.current;
    try {
      const next = await authApi.login(input);
      if (id === generation.current) {accept(next); setNotice(null);}
    } finally {actionPending.current = false;}
  };
  const logout = async () => {
    actionPending.current = true;
    const id = ++generation.current;
    try {
      await authApi.logout();
      if (id === generation.current) {accept({authenticated: false, user: null}); setNotice(null);}
    } finally {actionPending.current = false;}
  };
  return <Context.Provider value={{...session, status, loadError, notice, refresh, login, logout}}>{children}</Context.Provider>;
}
export function useAuth() {const value = useContext(Context); if (!value) throw new Error('useAuth requires AuthProvider'); return value;}
