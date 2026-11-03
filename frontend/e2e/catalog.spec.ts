import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';

// Подменённые ответы проверяют UI, а не интеграцию с Django.
const categories = [{id: 1, name: 'Инструменты'}, {id: 2, name: 'Уборка'}];
const products = [
  {id: 1, name: 'Дрель', category: 1, status: 'active', image_url: null, brand: 'Бренд', model: 'Д-1'},
  {id: 2, name: 'Пылесос', category: 2, status: 'active', image_url: '/broken-image.png', brand: null, model: null},
  {id: 3, name: 'Дрель компактная', category: 1, status: 'active', image_url: '/test-product.svg', brand: null, model: null},
];

async function mockCatalog(page: Page) {
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: false, user: null}}));
  await page.route('**/api/categories/', route => route.fulfill({json: categories}));
  await page.route('**/broken-image.png', route => route.fulfill({status: 404, body: ''}));
  await page.route('**/test-product.svg', route => route.fulfill({contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="green"/></svg>'}));
  await page.route('**/api/products/**', route => {
    const params = new URL(route.request().url()).searchParams;
    const search = (params.get('search') ?? '').toLowerCase();
    const category = params.get('category');
    return route.fulfill({json: products.filter(product => product.name.toLowerCase().includes(search) && (!category || String(product.category) === category))});
  });
}

test.beforeEach(async ({page}) => {await mockCatalog(page);});

test('mocked: public catalog, cards, image fallback and auth navigation', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(page.getByRole('article')).toHaveCount(3);
  await expect(page.getByRole('article').first()).toContainText('Инструменты');
  await expect(page.getByRole('article').first()).toContainText('Бренд · Д-1');
  await expect(page.getByText('Нет изображения', {exact: true})).toHaveCount(2);
  await expect(page.getByRole('img', {name: 'Дрель компактная'})).toBeVisible();
  await expect(page.getByRole('article').getByRole('link')).toHaveCount(3);
  await expect(page.getByRole('article').first().getByRole('link')).toHaveAttribute('href', '/products/1');
  await page.screenshot({path: 'test-results/catalog-desktop.png', fullPage: true});
  await page.getByRole('navigation').getByRole('link', {name: 'Войти', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Войти в БЕРИ'})).toBeVisible();
  await page.getByRole('navigation').getByRole('link', {name: 'Регистрация'}).click();
  await expect(page.getByRole('heading', {name: 'Создать аккаунт'})).toBeVisible();
  await page.getByRole('link', {name: 'БЕРИ на время'}).click();
  await expect(page.getByRole('article')).toHaveCount(3);
  expect(errors).toEqual([]);
});

test('mocked: search button, Enter, category, combined filters, empty and reset', async ({page}) => {
  await page.goto('/catalog');
  await expect(page.getByRole('article')).toHaveCount(3);
  await page.getByLabel('Название товара').fill('Дрель');
  await page.getByRole('button', {name: 'Найти', exact: true}).click();
  await expect(page.getByRole('article')).toHaveCount(2);
  const combined = page.waitForRequest(request => {
    const url = new URL(request.url());
    return url.pathname === '/api/products/' && url.searchParams.get('search') === 'Дрель' && url.searchParams.get('category') === '2';
  });
  await page.getByLabel('Категория', {exact: true}).selectOption('2');
  await combined;
  await expect(page.getByText('Товары не найдены', {exact: true})).toBeVisible();
  await page.getByLabel('Название товара').fill('Пылесос');
  await page.getByLabel('Название товара').press('Enter');
  await expect(page.getByRole('article')).toHaveCount(1);
  await expect(page.getByRole('article')).toContainText('Пылесос');
  await page.getByRole('button', {name: 'Сбросить'}).click();
  await expect(page.getByLabel('Название товара')).toHaveValue('');
  await expect(page.getByLabel('Категория', {exact: true})).toHaveValue('');
  await expect(page.getByRole('article')).toHaveCount(3);
  await page.getByLabel('Категория', {exact: true}).selectOption('1');
  await expect(page.getByRole('article')).toHaveCount(2);
  await page.getByLabel('Категория', {exact: true}).selectOption('');
  await expect(page.getByRole('article')).toHaveCount(3);
});

test('mocked: special characters are encoded in a single search parameter', async ({page}) => {
  await page.goto('/catalog');
  const search = 'Дрель & + #? /';
  await page.getByLabel('Название товара').fill(search);
  const requestPromise = page.waitForRequest(request => new URL(request.url()).searchParams.has('search'));
  await page.getByLabel('Название товара').press('Enter');
  const params = new URL((await requestPromise).url()).searchParams;
  expect([...params.entries()]).toEqual([['search', search]]);
  await expect(page.getByText('Товары не найдены', {exact: true})).toBeVisible();
});

test('mocked: loading is visible before the response', async ({page}) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/products/**', async route => {await pending; await route.fulfill({json: products});});
  await page.goto('/catalog');
  await expect(page.getByRole('status')).toHaveText('Загрузка…');
  await expect(page.getByRole('article')).toHaveCount(0);
  release();
  await expect(page.getByRole('article')).toHaveCount(3);
});

test('mocked: a late success or failure cannot replace the latest results', async ({page}) => {
  for (const fail of [false, true]) {
    let release!: () => void;
    const pending = new Promise<void>(resolve => {release = resolve;});
    await page.route('**/api/products/**', async route => {
      if (new URL(route.request().url()).searchParams.get('search') === 'старый') {
        await pending;
        await route.fulfill(fail ? {status: 500, json: {}} : {json: products});
      } else await route.fulfill({json: [products[1]]});
    });
    await page.goto('/catalog');
    await expect(page.getByRole('article')).toHaveCount(1);
    await page.getByLabel('Название товара').fill('старый');
    const oldRequest = page.waitForRequest('**/api/products/?search=*');
    await page.getByLabel('Название товара').press('Enter');
    await oldRequest;
    await page.getByLabel('Название товара').fill('новый');
    await page.getByLabel('Название товара').press('Enter');
    await expect(page.getByRole('article')).toContainText('Пылесос');
    const oldResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get('search') === 'старый');
    release();
    await (await oldResponse).finished();
    // Следующий кадр даёт fetch/effect обработать уже полученный старый ответ.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(page.getByRole('article')).toHaveCount(1);
    await expect(page.getByRole('article')).toContainText('Пылесос');
    await expect(page.getByRole('alert')).toHaveCount(0);
  }
});

for (const source of ['products', 'categories']) {
  test(`mocked: ${source} HTTP/network/contract errors and retry`, async ({page}) => {
    for (const kind of ['http', 'network', 'contract']) {
      await page.route(`**/api/${source}/**`, route => kind === 'network' ? route.abort()
        : route.fulfill(kind === 'http' ? {status: 503, json: {}} : {json: {results: []}}));
      await page.goto('/catalog');
      await expect(page.getByRole('alert')).toContainText('Не удалось загрузить каталог');
      await expect(page.getByText('Товары не найдены', {exact: true})).toHaveCount(0);
      await expect(page.getByRole('article')).toHaveCount(0);
      await page.unroute(`**/api/${source}/**`);
      await mockCatalog(page);
      await page.getByRole('button', {name: 'Повторить запрос'}).click();
      await expect(page.getByRole('article')).toHaveCount(3);
    }
  });
}

test('mocked: catalog remains public when the session check fails', async ({page}) => {
  await page.route('**/api/auth/me/', route => route.abort());
  await page.goto('/catalog');
  await expect(page.getByRole('article')).toHaveCount(3);
});
