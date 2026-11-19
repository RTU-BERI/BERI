import type {ReactNode} from 'react';
import {Link, useLocation} from 'react-router-dom';
import {useAuth} from '../auth/AuthProvider';

export function ReservationAccess({children}: {children: ReactNode}) {
  const auth = useAuth();
  const location = useLocation();
  if (auth.status === 'loading' || auth.loadError) return <div className="feedback" role="status">
    {auth.loadError ?? 'Проверяем сессию…'}{auth.loadError && <button type="button" onClick={() => void auth.refresh()}>Повторить проверку</button>}
  </div>;
  if (!auth.authenticated) return <p className="feedback">Для бронирований <Link to="/login"
    state={{returnTo: location.pathname + location.search}}>войдите в аккаунт</Link>.</p>;
  if (auth.user?.account_type !== 'customer') return <p className="feedback" role="alert">Бронирования доступны только клиентам.</p>;
  return children;
}
