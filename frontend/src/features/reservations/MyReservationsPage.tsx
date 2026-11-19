import {useCallback, useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {ApiError} from '../../shared/api/client';
import {useAuth} from '../auth/AuthProvider';
import {getProducts} from '../catalog/api';
import type {Product} from '../catalog/types';
import {getPickupPoints} from '../product/api';
import type {PickupPoint} from '../product/types';
import {getReservations, reservationError} from './api';
import {ReservationAccess} from './ReservationAccess';
import {ReservationCard} from './ReservationCard';
import type {Clock, Reservation} from './types';
import './reservations.css';

function ReservationsList() {
  const auth = useAuth();
  const [data, setData] = useState<{reservations: Reservation[]; clock: Clock} | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [points, setPoints] = useState<PickupPoint[]>([]);
  const [detailsError, setDetailsError] = useState(false);
  const mounted = useRef(false);
  const references = useRef<Promise<[PromiseSettledResult<Product[]>, PromiseSettledResult<PickupPoint[]>]> | null>(null);
  const inFlight = useRef<Promise<boolean> | null>(null);
  const dueChecked = useRef(new Set<string>());
  const refresh = useCallback((): Promise<boolean> => {
    if (inFlight.current) return inFlight.current;
    setRefreshing(true);
    const request = getReservations().then(next => {
      if (!mounted.current) return false;
      setData(next); setError(null); return true;
    }).catch(e => {
      if (mounted.current) {
        setError(reservationError(e));
        if (e instanceof ApiError && [401, 403].includes(e.status)) void auth.refresh();
      }
      return false;
    }).finally(() => {if (inFlight.current === request) inFlight.current = null;
      if (mounted.current) setRefreshing(false);
    });
    inFlight.current = request;
    return request;
  }, [auth.refresh]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    let active = true;
    references.current ??= Promise.allSettled([getProducts('', ''), getPickupPoints()]);
    void references.current.then(([catalog, pickup]) => {
      if (!active) return;
      if (catalog.status === 'fulfilled') setProducts(catalog.value);
      if (pickup.status === 'fulfilled') setPoints(pickup.value);
      setDetailsError(catalog.status === 'rejected' || pickup.status === 'rejected');
    });
    const onFocus = () => {if (document.visibilityState !== 'hidden') void refresh();};
    window.addEventListener('focus', onFocus); document.addEventListener('visibilitychange', onFocus);
    return () => {active = false; mounted.current = false;
      window.removeEventListener('focus', onFocus); document.removeEventListener('visibilitychange', onFocus);
    };
  }, [refresh]);
  const productMap = new Map(products.map(item => [item.id, item]));
  const pointMap = new Map(points.map(item => [item.id, item]));
  return <div className="reservations-list" aria-busy={refreshing}>
    {!data && refreshing && <div className="catalog-state" role="status">Загрузка бронирований…</div>}
    {error && <div className="feedback error" role="alert">{error}<button type="button" disabled={refreshing} onClick={() => void refresh()}>Повторить загрузку бронирований</button></div>}
    {detailsError && data && <p className="feedback">Часть сведений о товарах или пунктах выдачи недоступна. Бронирования показаны по данным сервера.</p>}
    {data && data.reservations.length === 0 && <div className="catalog-state"><h2>У вас пока нет бронирований</h2><p><Link to="/catalog">Перейти в каталог</Link></p></div>}
    {data && data.reservations.map(reservation => <ReservationCard key={reservation.id} reservation={reservation}
      product={productMap.get(reservation.product_id)} point={pointMap.get(reservation.pickup_point_id)} clock={data.clock} refresh={refresh}
      onElapsed={() => {const key = reservation.id + reservation.expires_at; if (!dueChecked.current.has(key)) {
        dueChecked.current.add(key); void refresh();
      }}}/>)}
    {data && <button type="button" className="secondary-button" disabled={refreshing} onClick={() => void refresh()}>{refreshing ? 'Обновляем…' : 'Обновить бронирования'}</button>}
  </div>;
}

export function MyReservationsPage() {
  const auth = useAuth();
  return <main className="reservations-page"><div className="reservations-heading"><p className="eyebrow">ВАШИ ВЕЩИ НА ВРЕМЯ</p>
    <h1>Мои бронирования</h1><p>История бронирований и время для получения товара.</p></div>
    <ReservationAccess><ReservationsList key={auth.user?.id}/></ReservationAccess>
  </main>;
}
