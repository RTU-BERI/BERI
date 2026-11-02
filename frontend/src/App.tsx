import {Link, Navigate, NavLink, Outlet, Route, Routes} from 'react-router-dom';
import {LoginPage} from './features/auth/LoginPage';
import {RegisterPage} from './features/auth/RegisterPage';
import {useAuth} from './features/auth/AuthProvider';
import {CatalogPage} from './features/catalog/CatalogPage';

function AuthLayout() {
  return <main><aside><p className="eyebrow">МЕНЬШЕ ПОКУПОК. БОЛЬШЕ ВОЗМОЖНОСТЕЙ.</p><h2>Бери для дела.<br/>Возвращай<br/><em>без забот.</em></h2><p>Нужные вещи — тогда, когда они нужны.</p><div className="graphic" aria-hidden="true"><span>Б</span><i>↗</i></div></aside><Outlet/></main>;
}

export function App() {
  const auth = useAuth();
  return <><header><Link className="brand" to="/catalog">БЕРИ<span>на время</span></Link>
    <nav aria-label="Основная навигация"><NavLink to="/catalog">Каталог</NavLink><NavLink to="/login">{auth.authenticated ? 'Мой аккаунт' : 'Войти'}</NavLink>{!auth.authenticated && <NavLink to="/register">Регистрация</NavLink>}</nav>
    <span className="demo-tag">Локальная демонстрация</span></header>
    <Routes>
      <Route path="/catalog" element={<CatalogPage/>}/>
      <Route element={<AuthLayout/>}><Route path="/login" element={<LoginPage/>}/><Route path="/register" element={<RegisterPage/>}/></Route>
      <Route path="*" element={<Navigate to="/catalog" replace/>}/>
    </Routes>
    <footer>БЕРИ · Вещи для жизни, без лишних покупок</footer></>;
}
