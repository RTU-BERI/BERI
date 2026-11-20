import {useEffect, useState} from 'react';
import {Link, Route, Routes, useParams} from 'react-router-dom';
import {useAuth} from '../auth/AuthProvider';
import {displayTime, employeeError, getEmployeeReservations, statusName} from './api';
import type {Reservation} from './api';
import {ReservationIssue} from './ReservationIssue';
import './employee.css';

function ReservationList() {
  const [rows, setRows] = useState<Reservation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    getEmployeeReservations().then(data => {
      if (active) setRows(data);
    }).catch(reason => {
      if (active) setError(employeeError(reason));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => {active = false;};
  }, [revision]);

  return <>
    <div className="employee-title"><h1>Бронирования пункта выдачи</h1>
      <button disabled={loading} onClick={() => setRevision(value => value + 1)}>Обновить список</button></div>
    {loading ? <p role="status">Загрузка бронирований…</p>
      : error ? <p role="alert" className="feedback error">{error}</p>
      : rows.length === 0 ? <p role="status">Бронирований пока нет.</p>
      : <table className="employee-table"><caption>Бронирования, доступные вашей учётной записи</caption>
        <thead><tr><th>Бронь</th><th>Товар / ПВ</th><th>Статус</th><th>Срок брони</th><th>Действие</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.id}>
          <td className="reservation-number">{row.id}</td>
          <td>Товар №{row.product_id}<br/>ПВ №{row.pickup_point_id}</td>
          <td>{statusName(row.status)}</td><td>{displayTime(row.expires_at)}</td>
          <td><Link to={`/employee/reservations/${row.id}`}>{row.status === 'active' ? 'Открыть бронь' : 'История'}</Link></td>
        </tr>)}</tbody>
      </table>}
  </>;
}

function ReservationRoute() {
  const {reservationId = ''} = useParams();
  return <ReservationIssue key={reservationId} reservationId={reservationId}/>;
}

export function EmployeePage() {
  const auth = useAuth();
  return <main className="employee-page">
    <p className="eyebrow">РАБОЧЕЕ МЕСТО СОТРУДНИКА</p>
    {auth.status === 'loading' ? <><p role="status">Проверяем сессию…</p>
      {auth.loadError && <><p role="alert">{auth.loadError}</p><button onClick={() => void auth.refresh()}>Повторить проверку</button></>}</>
      : !auth.authenticated ? <><h1>Войдите как сотрудник</h1><Link to="/login">Перейти ко входу</Link></>
      : auth.user?.account_type !== 'employee' ? <><h1>Доступ только сотрудникам</h1><p role="alert">Используйте учётную запись сотрудника пункта выдачи.</p></>
      : <Routes key={auth.user.id}>
        <Route index element={<ReservationList/>}/>
        <Route path=":reservationId" element={<ReservationRoute/>}/>
      </Routes>}
  </main>;
}
