import {Link, Navigate, Route, Routes} from 'react-router-dom';
import {LoginPage} from './features/auth/LoginPage';
import {RegisterPage} from './features/auth/RegisterPage';

// Minimal auth bootstrap only. S1-P5 will own the full shell/router and catalog.
export function App() {
  return <><header><Link className="brand" to="/login">БЕРИ<span>на время</span></Link><span className="demo-tag">Локальная демонстрация</span></header>
    <main><aside><p className="eyebrow">МЕНЬШЕ ПОКУПОК. БОЛЬШЕ ВОЗМОЖНОСТЕЙ.</p><h2>Бери для дела.<br/>Возвращай<br/><em>без забот.</em></h2><p>Нужные вещи — тогда, когда они нужны.</p><div className="graphic" aria-hidden="true"><span>Б</span><i>↗</i></div></aside><Routes><Route path="/login" element={<LoginPage/>}/><Route path="/register" element={<RegisterPage/>}/><Route path="*" element={<Navigate to="/login" replace/>}/></Routes></main>
    <footer>БЕРИ · Вещи для жизни, без лишних покупок</footer></>;
}
