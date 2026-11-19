import {useEffect, useRef, useState} from 'react';
import {clockNow} from './api';
import type {Clock, Reservation} from './types';

export function ReservationCountdown({reservation, clock, onElapsed}: {
  reservation: Reservation; clock: Clock; onElapsed: () => void;
}) {
  const [now, setNow] = useState(() => clockNow(clock));
  const checked = useRef('');
  const callback = useRef(onElapsed);
  callback.current = onElapsed;
  const active = reservation.status === 'active';
  useEffect(() => {
    setNow(clockNow(clock));
    if (!active) return;
    const interval = window.setInterval(() => setNow(clockNow(clock)), 1000);
    return () => window.clearInterval(interval);
  }, [clock, active]);
  const seconds = Math.max(0, Math.ceil((Date.parse(reservation.expires_at) - now) / 1000));
  const key = reservation.id + reservation.expires_at;
  useEffect(() => {
    if (active && seconds === 0 && checked.current !== key) {checked.current = key; callback.current();}
  }, [active, seconds, key]);
  if (!active) return null;
  return <p className="reservation-countdown">{seconds > 0
    ? <>Осталось: <time>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</time></>
    : 'Время истекло. Ожидаем подтверждения статуса сервером.'}
    {clock.source === 'device' && <span className="reservation-muted"> Ориентировочно, по часам устройства.</span>}
  </p>;
}
