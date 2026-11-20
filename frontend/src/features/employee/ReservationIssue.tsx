import {useCallback, useEffect, useRef, useState} from 'react';
import type {FormEvent} from 'react';
import {Link} from 'react-router-dom';
import {ApiError} from '../../shared/api/client';
import {displayTime, employeeError, getInspections, issueItem, recordHealthyInspection, replaceItem, statusName} from './api';
import type {InspectionHistory, ProblemType, Rental} from './api';

export function ReservationIssue({reservationId}: {reservationId: string}) {
  const [history, setHistory] = useState<InspectionHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [rental, setRental] = useState<Rental | null>(null);
  const [comment, setComment] = useState('');
  const [conditionChecked, setConditionChecked] = useState(false);
  const [result, setResult] = useState<'healthy' | 'problem'>('healthy');
  const [completenessChecked, setCompletenessChecked] = useState(false);
  const [problemType, setProblemType] = useState<ProblemType>('damage');
  const [description, setDescription] = useState('');
  const [reason, setReason] = useState('');
  const [cancelIfUnavailable, setCancelIfUnavailable] = useState(false);
  const [handoverConfirmed, setHandoverConfirmed] = useState(false);
  const inFlight = useRef(false);
  const generation = useRef(0);

  const load = useCallback(async () => {
    const request = ++generation.current;
    setLoading(true);
    setError('');
    try {
      const data = await getInspections(reservationId);
      if (request !== generation.current) return;
      setHistory(data);
      setNeedsRefresh(false);
      setConditionChecked(false);
      setResult('healthy');
      setCompletenessChecked(false);
      setHandoverConfirmed(false);
    } catch (failure) {
      if (request === generation.current) {
        setError(employeeError(failure));
        setNeedsRefresh(true);
      }
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, [reservationId]);

  useEffect(() => {
    void load();
    return () => {++generation.current;};
  }, [load]);

  const assignmentId = history?.expected_assignment_id;
  const currentInspection = history?.inspections.find(row => row.product_item_id === history.assigned_product_item_id);
  const healthy = currentInspection?.condition_ok && currentInspection.completeness_ok && currentInspection.problems.length === 0;
  const canAct = history?.reservation_status === 'active' && assignmentId != null && !needsRefresh && !loading && !busy;

  async function perform(operation: 'inspect' | 'replace' | 'issue') {
    if (!canAct || inFlight.current || assignmentId == null) return;
    inFlight.current = true;
    const request = generation.current;
    setBusy(true);
    setError('');
    setNotice('');
    setNeedsRefresh(true);
    try {
      if (operation === 'inspect') {
        await recordHealthyInspection(reservationId, assignmentId, comment);
      } else if (operation === 'replace') {
        await replaceItem(reservationId, {
          expected_assignment_id: assignmentId, condition_ok: false, completeness_ok: true,
          comment: comment || null,
          problems: [{problem_type: problemType, description: description.trim()}],
          reason: reason.trim(),
          ...(cancelIfUnavailable ? {completion_reason: 'Нет свободной замены'} : {}),
        });
      } else {
        const issued = await issueItem(reservationId, assignmentId);
        if (request === generation.current) setRental(issued);
      }
      if (request !== generation.current) return;
      setNotice(operation === 'inspect' ? 'Положительный осмотр сохранён.'
        : operation === 'replace' ? 'Проблема зафиксирована. Проверьте результат подбора замены ниже.'
        : 'Выдача выполнена. Аренда началась.');
      setComment('');
      setDescription('');
      setReason('');
      setCancelIfUnavailable(false);
      await load();
    } catch (failure) {
      if (request === generation.current) {
        const uncertain = !(failure instanceof ApiError) || failure.status === 0 || failure.status >= 500;
        setError(`${employeeError(failure)} Перед следующим действием обновите данные и проверьте историю.${uncertain ? ' Результат операции мог сохраниться на сервере.' : ''}`);
      }
    } finally {
      inFlight.current = false;
      // load() меняет generation, поэтому занятость снимается и после успешного GET.
      setBusy(false);
    }
  }

  function submitReplacement(event: FormEvent) {
    event.preventDefault();
    if (description.trim() && reason.trim() && completenessChecked) void perform('replace');
  }

  return <>
    <Link to="/employee/reservations">← Бронирования пункта выдачи</Link>
    <div className="employee-title"><h1>Осмотр и выдача</h1>
      <button disabled={busy || loading} onClick={() => void load()}>Обновить данные</button></div>
    <p className="reservation-number">Бронь {reservationId}</p>
    {notice && <p className="feedback" role="status">{notice}</p>}
    {rental && <div className="employee-success"><h2>Аренда началась</h2><p>Начало — {displayTime(rental.started_at)}</p>
      <Link to="/employee/reservations">Перейти к обновлённому списку</Link></div>}
    {error && <p className="feedback error" role="alert">{error}</p>}
    {loading ? <p role="status">Загрузка осмотров…</p> : history && <>
      <dl className="employee-summary"><div><dt>Статус брони</dt><dd>{statusName(history.reservation_status)}</dd></div>
        <div><dt>Текущий экземпляр</dt><dd>{history.assigned_product_item_id == null ? 'Назначение закрыто' : `№${history.assigned_product_item_id}`}</dd></div>
        <div><dt>Срок брони</dt><dd>{displayTime(history.expires_at)}</dd></div></dl>
      {history.reservation_status === 'cancelled' && <p role="status">Бронь отменена. Дальнейшие операции закрыты.</p>}
      {history.reservation_status === 'fulfilled' && !rental && <p role="status">Выдача уже оформлена.</p>}
      {history.reservation_status === 'expired' && <p role="status">Срок бронирования истёк.</p>}
      {history.reservation_status === 'active' && <section className="employee-panel">
        <h2>Проверка текущего экземпляра</h2>
        {currentInspection ? <p>{healthy ? 'Положительный осмотр текущего экземпляра сохранён.' : 'Осмотр уже сохранён. Проверьте историю.'}</p>
          : <fieldset disabled={!canAct}>
            <label>Комментарий к осмотру<textarea value={comment} onChange={event => setComment(event.target.value)}/></label>
            <label className="employee-check"><input type="checkbox" checked={completenessChecked} onChange={event => setCompletenessChecked(event.target.checked)}/>Комплектность проверена и соответствует</label>
            <label className="employee-check"><input type="radio" name="inspection-result" checked={result === 'healthy'} onChange={() => setResult('healthy')}/>Положительный осмотр</label>
            <label className="employee-check"><input type="radio" name="inspection-result" checked={result === 'problem'} onChange={() => setResult('problem')}/>Обнаружена проблема состояния</label>
            {result === 'healthy' ? <>
            <label className="employee-check"><input type="checkbox" checked={conditionChecked} onChange={event => setConditionChecked(event.target.checked)}/>Состояние проверено, проблем нет</label>
            <button disabled={!conditionChecked || !completenessChecked} onClick={() => void perform('inspect')}>Сохранить положительный осмотр</button>
            </> : <form className="employee-replacement" onSubmit={submitReplacement}>
              <h3>Обнаружена проблема состояния</h3>
              <label>Тип проблемы<select value={problemType} onChange={event => setProblemType(event.target.value as ProblemType)}>
                <option value="damage">Повреждение</option><option value="malfunction">Неисправность</option><option value="other">Другая проблема состояния</option>
              </select></label>
              <label>Описание проблемы<textarea required value={description} onChange={event => setDescription(event.target.value)}/></label>
              <label>Причина замены (до 30 символов)<input required maxLength={30} value={reason} onChange={event => setReason(event.target.value)}/></label>
              <p>Сервер подберёт свободный экземпляр того же товара в вашем пункте выдачи.</p>
              <label className="employee-check"><input type="checkbox" checked={cancelIfUnavailable} onChange={event => setCancelIfUnavailable(event.target.checked)}/>Если свободной замены нет, отменить бронь с причиной «Нет свободной замены»</label>
              <p className="employee-help">Если согласие на отмену не отмечено, отсутствие замены приведёт к отказу без сохранения проблемного осмотра. После успешной замены осмотрите новый экземпляр.</p>
              <button disabled={!completenessChecked || !description.trim() || !reason.trim()} type="submit">Зафиксировать проблему и заменить</button>
            </form>}
          </fieldset>}
        {healthy && <fieldset disabled={!canAct} className="employee-handover">
          <label className="employee-check"><input type="checkbox" checked={handoverConfirmed} onChange={event => setHandoverConfirmed(event.target.checked)}/>Подтверждаю фактическую передачу товара клиенту</label>
          <button disabled={!handoverConfirmed} onClick={() => void perform('issue')}>Подтвердить выдачу</button>
        </fieldset>}
      </section>}
      <section className="employee-panel"><h2>История осмотров</h2>
        {history.inspections.length === 0 ? <p>Осмотров пока нет.</p> : <ol className="employee-history">{history.inspections.map(row => <li key={row.id}>
          <strong>Экземпляр №{row.product_item_id}</strong> · {displayTime(row.inspected_at)}
          <p>{row.condition_ok ? 'Состояние исправное' : 'Есть проблема состояния'}. {row.completeness_ok ? 'Комплектность соответствует' : 'Есть недокомплектность'}.</p>
          {row.comment && <p>{row.comment}</p>}{row.problems.map((problem, index) => <p key={index}>{problem.description}</p>)}
        </li>)}</ol>}
      </section>
    </>}
    {busy && <p role="status">Сохраняем результат…</p>}
  </>;
}
