import {useCallback, useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {ApiError} from '../../shared/api/client';
import {useAuth} from '../auth/AuthProvider';
import {ProductImage} from '../product/ProductImage';
import {formatDecimal} from '../product/format';
import type {PickupPoint, ProductDetail} from '../product/types';
import {formatReservationDate, getReservations, reservationError} from './api';
import {beginOperation, forgetOperation, getOperation, sendOperation} from './operations';
import {ReservationAccess} from './ReservationAccess';
import {ReservationCountdown} from './ReservationCountdown';
import {statusLabels} from './types';
import type {ReservationResult} from './types';
import './reservations.css';

type Props = {product: ProductDetail; point?: PickupPoint; onCreated: () => void; onLock: (locked: boolean) => void};

function CreationForm({product, point, onCreated, onLock, accountId}: Props & {accountId: string}) {
  const auth = useAuth();
  const [operation, setOperation] = useState(() => getOperation(accountId));
  const [result, setResult] = useState<ReservationResult | undefined>(() => operation?.result);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const pending = useRef(false);
  const generation = useRef(0);
  const checking = useRef(false);
  const confirmButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {if (confirming) confirmButton.current?.focus();}, [confirming]);
  useEffect(() => () => {++generation.current;}, []);
  useEffect(() => {onLock(busy); return () => onLock(false);}, [busy, onLock]);

  const refreshResult = useCallback(async () => {
    if (!result || checking.current) return;
    checking.current = true;
    const current = generation.current;
    try {
      const data = await getReservations();
      if (generation.current !== current) return;
      const reservation = data.reservations.find(item => item.id === result.reservation.id);
      if (!reservation) {setCheckError('Не удалось найти бронь в актуальном списке. Откройте «Мои бронирования».'); return;}
      const next = {reservation, clock: data.clock};
      if (operation) operation.result = next;
      setResult(next); setCheckError(null);
    } catch (e) {
      if (generation.current !== current) return;
      setCheckError(`Не удалось проверить статус. ${reservationError(e)}`);
      if (e instanceof ApiError && [401, 403].includes(e.status)) void auth.refresh();
    } finally {checking.current = false;}
  }, [result, operation, auth.refresh]);
  useEffect(() => {
    if (!result) return;
    const onFocus = () => {if (document.visibilityState !== 'hidden') void refreshResult();};
    window.addEventListener('focus', onFocus); document.addEventListener('visibilitychange', onFocus);
    return () => {window.removeEventListener('focus', onFocus); document.removeEventListener('visibilitychange', onFocus);};
  }, [result, refreshResult]);

  async function submit() {
    if (pending.current || !point || !product.tariff) return;
    const op = operation ?? beginOperation(accountId, product.id, point.id);
    if (op.input.product_id !== product.id || op.input.pickup_point_id !== point.id) return;
    pending.current = true; setBusy(true); setError(null); setOperation(op);
    const current = generation.current;
    try {
      const next = await sendOperation(op);
      if (generation.current !== current) return;
      setResult(next); setConfirming(false); onCreated();
    } catch (e) {
      if (generation.current !== current) return;
      setError(reservationError(e));
      // A rejected HTTP operation is known not to have succeeded. Network/5xx/
      // malformed successful responses stay unresolved and retain their UUID.
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        forgetOperation(accountId); setOperation(undefined);
      }
      if (e instanceof ApiError && [401, 403].includes(e.status)) void auth.refresh();
    } finally {pending.current = false; if (generation.current === current) setBusy(false);}
  }

  const sameTarget = !operation || operation.input.product_id === product.id && operation.input.pickup_point_id === point?.id;
  if (!sameTarget) return <div className="feedback" role="status">
    <p>{operation?.result ? 'Результат предыдущего бронирования доступен в вашем списке.' : 'Результат предыдущей операции ещё не подтверждён. Сначала проверьте её, чтобы не создать вторую бронь.'}</p>
    <p><Link to={`/products/${operation!.input.product_id}?pickup_point=${operation!.input.pickup_point_id}`}>Вернуться к предыдущей операции</Link> · <Link to="/my-reservations">Мои бронирования</Link></p>
    {operation?.result && <button type="button" onClick={() => {forgetOperation(accountId); setOperation(undefined); setResult(undefined);}}>Начать новую операцию</button>}
  </div>;
  if (result) return <div className="reservation-result">
    <h3>{result.reservation.status === 'active' ? 'Товар забронирован' : `Бронирование: ${statusLabels[result.reservation.status]}`}</h3>
    <p>{product.name} · {point?.name}</p><p>{point?.address}</p>
    <p className="reservation-id">Бронирование № {result.reservation.id}</p>
    <p>Срок действия: <time dateTime={result.reservation.expires_at}>{formatReservationDate(result.reservation.expires_at)}</time></p>
    <ReservationCountdown reservation={result.reservation} clock={result.clock} onElapsed={() => void refreshResult()}/>
    {checkError && <div className="feedback error" role="alert">{checkError}<button type="button" onClick={() => void refreshResult()}>Повторить проверку статуса</button></div>}
    <p><Link to="/my-reservations">Мои бронирования</Link></p>
    <button type="button" className="secondary-button" onClick={() => {forgetOperation(accountId); setOperation(undefined); setResult(undefined); setConfirming(true);}}>Создать ещё одно бронирование</button>
  </div>;
  return <>
    {!point && <p className="reservation-muted">Выберите пункт выдачи для бронирования.</p>}
    {!product.tariff && <p className="reservation-muted">Бронирование недоступно: действующий тариф не указан.</p>}
    {error && <div className="feedback error" role="alert">{error}</div>}
    {operation && !result && <p className="feedback">Результат операции пока неизвестен. Повтор использует тот же идентификатор и не создаст дубликат.</p>}
    {confirming || operation ? <div className="reservation-confirmation">
      <h3>Подтвердите бронирование</h3>
      <div className="reservation-product-preview"><ProductImage image={product.image_url} name={product.name}/><div>
        <p>{product.name}</p><p>{point?.name}</p><p>{point?.address}</p>
        {product.tariff && <p>{formatDecimal(product.tariff.hourly_rate, false)} ₽/час</p>}
      </div></div>
      <p className="reservation-muted">Бронь удерживает экземпляр на 60 минут с момента создания на сервере. Это срок получения товара, а не продолжительность аренды.</p>
      <div className="reservation-actions"><button ref={confirmButton} type="button" disabled={busy || !point || !product.tariff} onClick={() => void submit()}>
        {busy ? 'Бронируем…' : operation ? 'Повторить эту операцию' : 'Подтвердить бронирование'}</button>
        {!operation && <button type="button" className="secondary-button" disabled={busy} onClick={() => setConfirming(false)}>Вернуться к товару</button>}
      </div>
    </div> : <button type="button" disabled={!point || !product.tariff} onClick={() => {setConfirming(true); setError(null);}}>Забронировать</button>}
  </>;
}

export function CreateReservation(props: Props) {
  const auth = useAuth();
  return <section className="reservation-create" aria-labelledby="reservation-create-title"><h2 id="reservation-create-title">Бронирование</h2>
    <ReservationAccess><CreationForm key={auth.user?.id} {...props} accountId={auth.user?.id ?? ''}/></ReservationAccess>
  </section>;
}
