import {useState} from 'react';
import type {FormEvent} from 'react';
import {Link, useLocation} from 'react-router-dom';
import {ApiError} from '../../shared/api/client';
import {useAuth} from './AuthProvider';
import {FormFeedback} from './FormFeedback';

export function LoginPage() {
  const auth = useAuth(); const location = useLocation();
  const [phone, setPhone] = useState(''); const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState<ApiError | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try {await auth.login({phone, password}); setPassword('');}
    catch (e) {setError(e instanceof ApiError ? e : new ApiError('unknown', 'Не удалось войти. Повторите попытку.'));}
    finally {setBusy(false);}
  }
  async function signOut() {
    setBusy(true); setError(null);
    try {await auth.logout();} catch (e) {setError(e instanceof ApiError ? e : new ApiError('unknown', 'Не удалось выйти.'));}
    finally {setBusy(false);}
  }
  if (auth.status === 'loading') return <section className="panel"><p role="status">Проверяем сессию…</p>{auth.loadError && <><p role="alert">{auth.loadError}</p><button onClick={() => void auth.refresh()}>Повторить проверку</button></>}</section>;
  if (auth.authenticated && auth.user) return <section className="panel"><span className="eyebrow">ВАШ АККАУНТ</span><h1>Вы вошли</h1><p className="intro">{auth.user.phone}</p><p>{auth.user.account_type === 'employee' ? 'Сотрудник пункта выдачи' : 'Клиент'}</p><div className="feedback">{auth.user.phone_verified ? 'Телефон подтверждён' : 'Телефон не подтверждён. SMS-подтверждение пока не подключено.'}</div><FormFeedback error={error}/><button disabled={busy} onClick={() => void signOut()}>{busy ? 'Выходим…' : 'Выйти'}</button></section>;
  return <section className="panel"><span className="eyebrow">С ВОЗВРАЩЕНИЕМ</span><h1>Войти в БЕРИ</h1><p className="intro">Ваши вещи на время. Ваш аккаунт — всегда под рукой.</p>
    {location.state?.registered && <div role="status" className="feedback">Аккаунт создан. Теперь войдите с номером телефона и паролем. Телефон пока не подтверждён.</div>}
    {auth.notice && <p role="status" className="feedback">{auth.notice}</p>}
    <FormFeedback error={error}/><form onSubmit={submit}>
      <label>Телефон<input type="tel" autoComplete="username" required value={phone} onChange={e => setPhone(e.target.value)} placeholder="+7 (999) 123-45-67"/></label>
      <label>Пароль<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)}/></label>
      <button disabled={busy}>{busy ? 'Входим…' : 'Войти'}</button>
    </form><p className="switch">Ещё нет аккаунта? <Link to="/register">Зарегистрироваться</Link></p></section>;
}
