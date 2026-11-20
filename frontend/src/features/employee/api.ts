import {api, ApiError} from '../../shared/api/client';

export type Reservation = {
  id: string;
  product_id: number;
  pickup_point_id: number;
  assigned_product_item_id: number | null;
  status: string;
  expires_at: string;
};

export type Inspection = {
  id: number;
  product_item_id: number;
  condition_ok: boolean;
  completeness_ok: boolean;
  comment: string | null;
  inspected_at: string;
  problems: {problem_type: string; description: string}[];
};

export type InspectionHistory = {
  reservation_id: string;
  reservation_status: string;
  assigned_product_item_id: number | null;
  expected_assignment_id: number | null;
  expires_at: string;
  inspections: Inspection[];
};

export type Rental = {id: string; status: string; started_at: string};
export type ProblemType = 'damage' | 'malfunction' | 'other';
export type ReplacementInput = {
  expected_assignment_id: number;
  condition_ok: false;
  completeness_ok: true;
  comment: string | null;
  problems: {problem_type: ProblemType; description: string}[];
  reason: string;
  completion_reason?: string;
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function id(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

function invalidResponse(): never {
  throw new ApiError('invalid_response', 'Не удалось прочитать ответ сервера. Обновите данные.');
}

function reservationPath(reservationId: string) {
  return `/api/employee/reservations/${encodeURIComponent(reservationId)}/`;
}

export async function getEmployeeReservations(): Promise<Reservation[]> {
  const data = await api<unknown>('/api/employee/reservations/');
  if (!Array.isArray(data)) invalidResponse();
  return data.map(row => {
    if (!record(row) || typeof row.id !== 'string' || !id(row.product_id)
      || !id(row.pickup_point_id) || typeof row.status !== 'string'
      || typeof row.expires_at !== 'string'
      || !(row.assigned_product_item_id === null || id(row.assigned_product_item_id))) invalidResponse();
    // Телефон клиента из исходного ответа не сохраняется в состоянии экрана.
    return {
      id: row.id, product_id: row.product_id, pickup_point_id: row.pickup_point_id,
      status: row.status, expires_at: row.expires_at,
      assigned_product_item_id: row.assigned_product_item_id,
    };
  });
}

export async function getInspections(reservationId: string): Promise<InspectionHistory> {
  const data = await api<unknown>(`${reservationPath(reservationId)}inspections/`);
  if (!record(data) || data.reservation_id !== reservationId
    || typeof data.reservation_status !== 'string' || typeof data.expires_at !== 'string'
    || !(data.assigned_product_item_id === null || id(data.assigned_product_item_id))
    || !(data.expected_assignment_id === null || id(data.expected_assignment_id))
    || !Array.isArray(data.inspections)) invalidResponse();
  for (const row of data.inspections) {
    if (!record(row) || !id(row.id) || !id(row.product_item_id)
      || typeof row.condition_ok !== 'boolean' || typeof row.completeness_ok !== 'boolean'
      || !(row.comment === null || typeof row.comment === 'string')
      || typeof row.inspected_at !== 'string' || !Array.isArray(row.problems)
      || !row.problems.every(problem => record(problem) && typeof problem.problem_type === 'string'
        && typeof problem.description === 'string')) invalidResponse();
  }
  return data as InspectionHistory;
}

export function recordHealthyInspection(reservationId: string, assignmentId: number, comment: string) {
  return api(`${reservationPath(reservationId)}inspections/`, 'POST', {
    expected_assignment_id: assignmentId, condition_ok: true, completeness_ok: true,
    comment: comment || null, problems: [],
  });
}

export function replaceItem(reservationId: string, input: ReplacementInput) {
  // replacement_item_id пропущен: подходящий экземпляр подбирает Django.
  return api(`${reservationPath(reservationId)}replace/`, 'POST', input);
}

export async function issueItem(reservationId: string, assignmentId: number): Promise<Rental> {
  const data = await api<unknown>(`${reservationPath(reservationId)}issue/`, 'POST', {
    expected_assignment_id: assignmentId,
  });
  if (!record(data) || typeof data.id !== 'string' || data.status !== 'active'
    || typeof data.started_at !== 'string') invalidResponse();
  return {id: data.id, status: data.status, started_at: data.started_at};
}

export function employeeError(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Не удалось выполнить запрос. Обновите данные.';
  switch (error.status) {
    case 400:
      return 'Сервер отклонил данные. Проверьте описание, причину и согласие на отмену при отсутствии замены.';
    case 401:
    case 403:
      return 'Доступ запрещён или сессия завершилась. Войдите под учётной записью сотрудника с правами своего пункта выдачи. Если вход выполнен, обновите страницу для проверки сессии и CSRF.';
    case 404:
      return 'Бронирование не найдено или недоступно в вашем пункте выдачи.';
    case 409:
      return 'Состояние изменилось или условия операции не выполнены. Возможны истечение брони, другое назначение или ограничения выдачи. Обновите данные.';
    default:
      return 'Не удалось получить подтверждение сервера. Проверьте соединение и обновите данные.';
  }
}

export function statusName(status: string) {
  return ({active: 'Действует', fulfilled: 'Выдано', cancelled: 'Отменено', expired: 'Истекло'} as Record<string, string>)[status] ?? status;
}

export function displayTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Дата недоступна' : date.toLocaleString('ru-RU');
}
