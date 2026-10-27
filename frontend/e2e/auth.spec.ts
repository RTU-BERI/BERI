import {expect, test} from '@playwright/test';

test('real API: register, errors, login, persistent me, CSRF rotation, logout', async ({page, context}) => {
  const phone = process.env.BERI_E2E_PHONE ?? '+12025550199';
  const password = 'Browser-synthetic-password9!Guitar';
  const failures: string[] = [];
  page.on('pageerror', error => failures.push(error.message));
  page.on('request', request => {
    if (request.resourceType() === 'fetch') expect(request.url().startsWith('http://localhost:5173/api/')).toBeTruthy();
  });
  await page.goto('/register');
  await expect(page.getByRole('heading', {name: 'Создать аккаунт'})).toBeVisible();
  await page.getByLabel('Телефон', {exact: true}).fill(phone);
  await page.getByLabel('Пароль', {exact: true}).fill(password);
  await page.getByLabel('Повторите пароль').fill('mismatch');
  await page.getByRole('button', {name: 'Зарегистрироваться'}).click();
  await expect(page.getByRole('alert')).toContainText('Пароли не совпадают');
  await page.getByLabel('Повторите пароль').fill(password);
  await page.getByRole('button', {name: 'Зарегистрироваться'}).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole('status')).toContainText('Аккаунт создан');
  await page.getByLabel('Телефон', {exact: true}).fill(phone);
  await page.getByLabel('Пароль', {exact: true}).fill('wrong');
  await page.getByRole('button', {name: 'Войти', exact: true}).click();
  await expect(page.getByRole('alert')).toContainText('Неверный номер телефона или пароль');
  const before = (await context.cookies()).find(c => c.name === 'csrftoken')?.value;
  await page.getByLabel('Пароль', {exact: true}).fill(password);
  await page.getByRole('button', {name: 'Войти', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Вы вошли'})).toBeVisible();
  const cookies = await context.cookies();
  expect(cookies.find(c => c.name === 'sessionid')?.httpOnly).toBeTruthy();
  expect(cookies.find(c => c.name === 'csrftoken')?.value).not.toBe(before);
  const me = await page.evaluate(async () => (await fetch('/api/auth/me/')).json());
  expect(me.authenticated).toBeTruthy();
  expect(me.user.phone_verified).toBeFalsy();
  await expect(page.getByText('Телефон не подтверждён.', {exact: false})).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', {name: 'Вы вошли'})).toBeVisible();
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await page.getByRole('button', {name: 'Выйти', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Войти в БЕРИ'})).toBeVisible();
  expect(await page.evaluate(async () => (await (await fetch('/api/auth/me/')).json()).authenticated)).toBeFalsy();
  await page.goto('/register');
  await page.getByLabel('Телефон', {exact: true}).fill(phone);
  await page.getByLabel('Пароль', {exact: true}).fill(password);
  await page.getByLabel('Повторите пароль').fill(password);
  await page.getByRole('button', {name: 'Зарегистрироваться'}).click();
  await expect(page.getByRole('alert')).toContainText('Этот номер уже зарегистрирован');
  await page.screenshot({path: process.env.BERI_SCREENSHOT_PATH ?? 'test-results/register-desktop.png', fullPage: true});
  expect(failures).toEqual([]);
});

test('invalid phone is rendered from real server validation', async ({page}) => {
  await page.goto('/register');
  await page.getByLabel('Телефон', {exact: true}).fill('8999');
  await page.getByLabel('Пароль', {exact: true}).fill('Browser-synthetic-password9!Guitar');
  await page.getByLabel('Повторите пароль').fill('Browser-synthetic-password9!Guitar');
  await page.getByRole('button', {name: 'Зарегистрироваться'}).click();
  await expect(page.getByRole('alert')).toContainText('Введите международный номер');
});

test('registration unavailable, rate limit, csrf and network failures are visible', async ({page}) => {
  await page.goto('/register');
  await page.getByLabel('Телефон', {exact: true}).fill('+12025550198');
  await page.getByLabel('Пароль', {exact: true}).fill('Browser-synthetic-password9!Guitar');
  await page.getByLabel('Повторите пароль').fill('Browser-synthetic-password9!Guitar');
  for (const [code, status, message] of [['registration_unavailable', 503, 'Демонстрационная регистрация недоступна.'], ['rate_limited', 429, 'Слишком много попыток.'], ['csrf_failed', 403, 'Обновите страницу.']] as const) {
    await page.route('**/api/auth/register/', route => route.fulfill({status, json: {error: {code, message}}}));
    await page.getByRole('button', {name: 'Зарегистрироваться'}).click();
    await expect(page.getByRole('alert')).toContainText(message);
    await page.unroute('**/api/auth/register/');
  }
  await page.route('**/api/auth/register/', route => route.abort());
  await page.getByRole('button', {name: 'Зарегистрироваться'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось связаться с сервером');
});

test('initial loading and session check network error can be retried', async ({page}) => {
  await page.route('**/api/auth/me/', async route => {await new Promise(resolve => setTimeout(resolve, 300)); await route.abort();});
  await page.goto('/login');
  await expect(page.getByRole('status')).toContainText('Проверяем сессию');
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить сессию');
  await page.unroute('**/api/auth/me/');
  await page.getByRole('button', {name: 'Повторить проверку'}).click();
  await expect(page.getByRole('heading', {name: 'Войти в БЕРИ'})).toBeVisible();
});

test('expired session on focus clears auth state', async ({page}) => {
  const user = {id: 'synthetic-ui-fixture', phone: '+12025550197', account_type: 'customer', phone_verified: false};
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: true, user}}));
  await page.goto('/login');
  await expect(page.getByRole('heading', {name: 'Вы вошли'})).toBeVisible();
  await page.unroute('**/api/auth/me/');
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('status')).toContainText('Сессия завершилась');
  await expect(page.getByRole('heading', {name: 'Войти в БЕРИ'})).toBeVisible();
});
