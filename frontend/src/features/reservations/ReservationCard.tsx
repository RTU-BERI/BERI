import {useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {ApiError} from '../../shared/api/client';
import {useAuth} from '../auth/AuthProvider';
import type {Product} from '../catalog/types';
import {ProductImage} from '../product/ProductImage';
import type {PickupPoint} from '../product/types';
import {cancelReservation, clockNow, formatReservationDate, reservationError} from './api';
import {ReservationCountdown} from './ReservationCountdown';
import {statusLabels} from './types';
import type {Clock, Reservation} from './types';

export function ReservationCard({reservation, product, point, clock, refresh, onElapsed}: {
  reservation: Reservation; product?: Product; point?: PickupPoint; clock: Clock;
  refresh: () => Promise<boolean>; onElapsed: () => void;
}) {
  const auth = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [elapsed, setElapsed] = useState(() => Date.parse(reservation.expires_at) <= clockNow(clock));
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);
  const mounted = useRef(false);
  const safeAction = useRef<HTMLButtonElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {if (confirming) safeAction.current?.focus();}, [confirming]);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  useEffect(() => {setElapsed(Date.parse(reservation.expires_at) <= clockNow(clock));}, [clock, reservation.expires_at]);
  const canCancel = reservation.status === 'active' && !elapsed && !accepted;
  async function cancel() {
    if (pending.current || !canCancel) return;
    pending.current = true; setBusy(true); setError(null);
    try {
      await cancelReservation(reservation.id);
      if (!mounted.current) return;
      setAccepted(true); setConfirming(false);
      const updated = await refresh();
      if (mounted.current && !updated) setError('Отмена принята сервером, но список не обновился. Повторите загрузку.');
    } catch (e) {
      if (!mounted.current) return;
      setError(reservationError(e)); setConfirming(false);
      if (e instanceof ApiError && [401, 403].includes(e.status)) void auth.refresh();
      if (e instanceof ApiError && [404, 409].includes(e.status)) await refresh();
    } finally {pending.current = false; if (mounted.current) setBusy(false);}
  }
  const name = product?.name ?? `Товар № ${reservation.product_id}`;
  return <article className="reservation-card" aria-label={`Бронирование ${reservation.id}`}>
    <ProductImage image={product?.image_url ?? null} name={name}/>
    <div className="reservation-card__body"><h2><Link to={`/products/${reservation.product_id}`}>{name}</Link></h2>
      <p className="reservation-id">№ {reservation.id}</p><p className="reservation-status">Статус: {statusLabels[reservation.status]}</p>
      <p>{point?.name ?? `Пункт выдачи № ${reservation.pickup_point_id}`}</p>
      <p className="reservation-muted">{point?.address ?? 'Адрес пункта выдачи сейчас недоступен.'}</p>
      <dl><div><dt>Создано</dt><dd><time dateTime={reservation.created_at}>{formatReservationDate(reservation.created_at)}</time></dd></div>
        <div><dt>Срок действия</dt><dd><time dateTime={reservation.expires_at}>{formatReservationDate(reservation.expires_at)}</time></dd></div></dl>
      <ReservationCountdown reservation={reservation} clock={clock} onElapsed={() => {setElapsed(true); setConfirming(false); onElapsed();}}/>
      {accepted && <p className="feedback" role="status">Отмена бронирования принята сервером.</p>}
      {error && <p className="feedback error" role="alert">{error}</p>}
      {canCancel && (confirming ? <div className="reservation-cancel"><p>Отменить бронирование?</p><div className="reservation-actions">
        <button ref={safeAction} type="button" className="secondary-button" disabled={busy} onClick={() => {setConfirming(false); requestAnimationFrame(() => cancelButton.current?.focus());}}>Оставить бронирование</button>
        <button type="button" disabled={busy} onClick={() => void cancel()}>{busy ? 'Отменяем…' : 'Отменить бронирование'}</button>
      </div></div> : <button ref={cancelButton} type="button" className="secondary-button" onClick={() => setConfirming(true)}>Отменить бронирование</button>)}
    </div>
  </article>;
}
