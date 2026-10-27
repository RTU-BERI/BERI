import {ApiError} from '../../shared/api/client';

const labels: Record<string, string> = {
  invalid_phone: 'Введите международный номер: + и код страны.',
  password_mismatch: 'Пароли не совпадают.', password_too_short: 'Пароль слишком короткий — минимум 8 символов.',
  password_too_common: 'Этот пароль слишком распространён.', password_entirely_numeric: 'Пароль не должен состоять только из цифр.',
  password_too_similar: 'Пароль слишком похож на ваши данные.', required: 'Заполните поле.', blank: 'Заполните поле.',
};
export function FormFeedback({error}: {error: ApiError | null}) {
  if (!error) return null;
  return <div role="alert" className="feedback error"><strong>{error.message}</strong>{Object.entries(error.fields).map(([field, codes]) =>
    <p key={field}>{(Array.isArray(codes) ? codes : []).map(code => labels[code] ?? 'Проверьте значение поля.').join(' ')}</p>)}</div>;
}
