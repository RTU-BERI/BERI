import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';
import {availability, mockProductApi, otherProduct, pickupPoints, product} from './product-fixtures';

test.beforeEach(async ({page}) => {await mockProductApi(page);});

async function openProduct(page: Page) {
  await page.goto('/products/1');
  await expect(page.getByRole('heading', {name: product.name, exact: true})).toBeVisible();
}

test('mocked: keyboard navigation from catalog, details, layout, price and local pickup changes', async ({page}) => {
  const errors: string[] = [];
  const requests: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {if (request.url().endsWith('/availability/')) requests.push(request.url());});
  await page.goto('/catalog');
  const link = page.getByRole('link', {name: product.name, exact: true});
  await expect(link).toBeVisible();
  await page.screenshot({path: 'test-results/product-catalog-desktop.png', fullPage: true});
  for (const target of [
    page.getByRole('link', {name: 'БЕРИ на время', exact: true}),
    page.getByRole('navigation').getByRole('link', {name: 'Каталог', exact: true}),
    page.getByRole('navigation').getByRole('link', {name: 'Войти', exact: true}),
    page.getByRole('navigation').getByRole('link', {name: 'Регистрация', exact: true}),
    page.getByRole('searchbox', {name: 'Название товара'}),
    page.getByLabel('Категория', {exact: true}),
    page.getByRole('button', {name: 'Найти', exact: true}),
    page.getByRole('button', {name: 'Сбросить', exact: true}),
    link,
  ]) {
    await page.keyboard.press('Tab');
    await expect(target).toBeFocused();
  }
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/products\/1$/);
  await expect(page.getByRole('heading', {name: product.name, exact: true})).toBeVisible();
  await expect(page.getByText(product.description!, {exact: true})).toBeVisible();
  await expect(page.getByText('125,50 ₽/час', {exact: true})).toBeVisible();
  await expect(page.getByText('750 Вт', {exact: true})).toBeVisible();
  await expect(page.getByText('0 кг', {exact: true})).toBeVisible();
  await expect(page.getByRole('definition').filter({hasText: /^Нет$/})).toHaveCount(1);
  await expect(page.getByText('Металл', {exact: true})).toBeVisible();
  await expect(page.getByRole('region', {name: 'Комплектация'})).toContainText('Насадки2 шт.');
  await expect(page.getByText('Нет изображения', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: /Забронировать/})).toHaveCount(0);
  const backLink = page.getByRole('link', {name: '← В каталог'});
  const pickup = page.getByLabel('Выберите пункт выдачи', {exact: true});
  await page.keyboard.press('Tab');
  await expect(backLink).toBeFocused();
  await expect(backLink).toHaveCSS('outline-style', 'solid');
  await expect(backLink).toHaveCSS('outline-width', '3px');
  await page.keyboard.press('Tab');
  await expect(pickup).toBeFocused();
  await expect(pickup).toHaveCSS('outline-style', 'solid');
  await expect(pickup).toHaveCSS('outline-width', '3px');
  await expect(pickup).toHaveValue('');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(pickup).toHaveValue('11');
  await expect(page.getByText('Свободных экземпляров: 3', {exact: true})).toBeVisible();
  await expect(page.getByText(pickupPoints[0].address, {exact: true})).toBeVisible();
  const initialCount = requests.length;
  await page.screenshot({path: 'test-results/product-desktop.png', fullPage: true});
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(pickup).toHaveValue('22');
  await expect(page.getByText('Нет в наличии', {exact: true})).toBeVisible();
  await expect(page.getByText('Свободных экземпляров: 3', {exact: true})).toHaveCount(0);
  await expect(page.getByText(pickupPoints[1].address, {exact: true})).toBeVisible();
  await expect(page.getByText(pickupPoints[0].address, {exact: true})).toHaveCount(0);
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('Enter');
  await expect(pickup).toHaveValue('11');
  await expect(page.getByText('Свободных экземпляров: 3', {exact: true})).toBeVisible();
  expect(requests.length).toBe(initialCount); // Switching does not issue another all-points request.
  const image = await page.locator('.product-page__image').boundingBox();
  const info = await page.locator('.product-page__summary > .product-page__panel').boundingBox();
  expect(image!.x + image!.width).toBeLessThan(info!.x);
  expect(Math.abs(image!.y - info!.y)).toBeLessThan(2);
  await page.keyboard.press('Shift+Tab');
  await expect(backLink).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('article')).toHaveCount(2);
  expect(errors).toEqual([]);
});

test('mocked: empty optional fields, no current tariff, no pickup points and direct URL reload', async ({page}) => {
  await page.route('**/api/products/1/', route => route.fulfill({json: {...product,
    description: '', attributes: [], components: [], tariff: null, brand: null, model: null,
  }}));
  await page.route('**/api/pickup-points/', route => route.fulfill({json: []}));
  await page.route('**/api/products/1/availability/', route => route.fulfill({json: {product_id: 1, pickup_points: []}}));
  await openProduct(page);
  await expect(page.getByText('Описание пока не добавлено.', {exact: true})).toBeVisible();
  await expect(page.getByText('Характеристики пока не указаны.', {exact: true})).toBeVisible();
  await expect(page.getByRole('heading', {name: 'Комплектация'})).toHaveCount(0);
  await expect(page.getByText('Стоимость аренды сейчас недоступна.', {exact: true})).toBeVisible();
  await expect(page.getByText(/₽\/час/)).toHaveCount(0);
  await expect(page.getByText('Пункты выдачи пока не добавлены.', {exact: true})).toBeVisible();
  await expect(page.getByLabel('Выберите пункт выдачи', {exact: true})).toBeDisabled();
  await expect(page.getByText('Нет в наличии', {exact: true})).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', {name: product.name, exact: true})).toBeVisible();
});

test('mocked: missing availability is distinct from zero stock', async ({page}) => {
  await page.route('**/api/products/1/availability/', route => route.fulfill({json: {...availability, pickup_points: [availability.pickup_points[1]]}}));
  await openProduct(page);
  await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption('22');
  await expect(page.getByText('Нет в наличии', {exact: true})).toBeVisible();
  await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption('11');
  await expect(page.getByText('Данные о наличии в этом пункте недоступны.', {exact: true})).toBeVisible();
  await expect(page.getByText('Нет в наличии', {exact: true})).toHaveCount(0);
});

test('mocked: successful and broken images, long text and exact decimal display', async ({page}) => {
  const longName = 'Учебный товар с длинным названием '.repeat(5).trim();
  const longDescription = 'Длинное описание без сокращений. '.repeat(150);
  await page.route('**/api/products/1/', route => route.fulfill({json: {...product, name: longName,
    description: longDescription, image_url: '/test-product.svg', tariff: {...product.tariff, hourly_rate: '9999999999.99'},
    attributes: [{name: 'Точное значение', unit: null, value_text: null, value_number: '12345678901234.123456', value_boolean: null}],
  }}));
  await page.route('**/test-product.svg', route => route.fulfill({contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="green"/></svg>'}));
  await page.goto('/products/1');
  await expect(page.getByRole('img', {name: longName, exact: true})).toBeVisible();
  await expect(page.getByText('9 999 999 999,99 ₽/час', {exact: true})).toBeVisible();
  await expect(page.getByText('12 345 678 901 234,123456', {exact: true})).toBeVisible();
  await expect(page.getByText(longDescription.trim(), {exact: true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.route('**/test-product.svg', route => route.fulfill({status: 404, body: ''}));
  await page.reload();
  await expect(page.getByText('Нет изображения', {exact: true})).toBeVisible();
  await expect(page.getByRole('img', {name: longName, exact: true})).toHaveCount(0);
});

test('mocked: product loading, genuine 404 and invalid route IDs', async ({page}) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/products/1/', async route => {await pending; await route.fulfill({json: product});});
  await page.goto('/products/1');
  await expect(page.getByText('Загрузка товара…', {exact: true})).toBeVisible();
  await expect(page.getByRole('heading', {name: product.name, exact: true})).toHaveCount(0);
  release();
  await expect(page.getByRole('heading', {name: product.name, exact: true})).toBeVisible();
  await page.goto('/products/404');
  await expect(page.getByRole('heading', {name: 'Товар не найден'})).toBeVisible();
  const returnLink = page.locator('.catalog-state').getByRole('link', {name: 'Вернуться в каталог', exact: true});
  await expect(returnLink).toHaveAttribute('href', '/catalog');
  await page.screenshot({path: 'test-results/product-not-found.png', fullPage: true});
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', {name: 'БЕРИ на время', exact: true})).toBeFocused();
  for (let step = 0; step < 5; step++) await page.keyboard.press('Tab');
  await expect(returnLink).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/catalog$/);
  const invalidRequests: string[] = [];
  page.on('request', request => {if (/\/api\/products\/[^/]+\/$/.test(request.url())) invalidRequests.push(request.url());});
  for (const id of ['abc', '0', '-1', '9007199254740992']) {
    await page.goto(`/products/${id}`);
    await expect(page.getByRole('heading', {name: 'Товар не найден'})).toBeVisible();
    await expect(page.locator('.catalog-state').getByRole('link', {name: 'Вернуться в каталог', exact: true})).toHaveAttribute('href', '/catalog');
  }
  expect(invalidRequests).toEqual([]);
});

for (const kind of ['http', 'network', 'contract', 'html']) {
  test(`mocked: product ${kind} error and successful retry`, async ({page}) => {
    await page.route('**/api/products/1/', route => kind === 'network' ? route.abort()
      : route.fulfill(kind === 'http' ? {status: 503, json: {detail: 'Unavailable'}}
        : kind === 'html' ? {status: 502, contentType: 'text/html', body: '<h1>Bad gateway</h1>'} : {json: {...product, tariff: {hourly_rate: 0}}}));
    await page.goto('/products/1');
    await expect(page.getByRole('alert')).toContainText('Не удалось загрузить товар');
    await expect(page.getByRole('alert').getByRole('link', {name: 'Вернуться в каталог', exact: true})).toHaveAttribute('href', '/catalog');
    await expect(page.getByText('Нет в наличии', {exact: true})).toHaveCount(0);
    await page.unroute('**/api/products/1/');
    await page.getByRole('button', {name: 'Повторить загрузку товара'}).click();
    await expect(page.getByRole('heading', {name: product.name, exact: true})).toBeVisible();
  });
}

test('mocked: load-error return link is reachable and works with the keyboard', async ({page}) => {
  await page.route('**/api/products/1/', route => route.fulfill({status: 503, json: {detail: 'Unavailable'}}));
  await page.goto('/products/1');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('Не удалось загрузить товар');
  const returnLink = alert.getByRole('link', {name: 'Вернуться в каталог', exact: true});
  await page.screenshot({path: 'test-results/product-load-error.png', fullPage: true});
  for (const target of [
    page.getByRole('link', {name: 'БЕРИ на время', exact: true}),
    page.getByRole('navigation').getByRole('link', {name: 'Каталог', exact: true}),
    page.getByRole('navigation').getByRole('link', {name: 'Войти', exact: true}),
    page.getByRole('navigation').getByRole('link', {name: 'Регистрация', exact: true}),
    page.getByRole('link', {name: '← В каталог'}),
    alert.getByRole('button', {name: 'Повторить загрузку товара'}),
    returnLink,
  ]) {
    await page.keyboard.press('Tab');
    await expect(target).toBeFocused();
  }
  await expect(returnLink).toHaveCSS('outline-style', 'solid');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(page.getByRole('article')).toHaveCount(2);
});

test('mocked: pickup and availability loading, with selection changing before the response', async ({page}) => {
  let releasePoints!: () => void;
  let releaseAvailability!: () => void;
  const pointsPending = new Promise<void>(resolve => {releasePoints = resolve;});
  const stockPending = new Promise<void>(resolve => {releaseAvailability = resolve;});
  await page.route('**/api/pickup-points/', async route => {await pointsPending; await route.fulfill({json: pickupPoints});});
  await page.route('**/api/products/1/availability/', async route => {await stockPending; await route.fulfill({json: availability});});
  await openProduct(page);
  await expect(page.getByText('Загрузка пунктов выдачи…', {exact: true})).toBeVisible();
  await expect(page.getByLabel('Выберите пункт выдачи', {exact: true})).toBeDisabled();
  releasePoints();
  await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption('11');
  await expect(page.getByText('Загрузка наличия…', {exact: true})).toBeVisible();
  await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption('22');
  releaseAvailability();
  await expect(page.getByText('Нет в наличии', {exact: true})).toBeVisible();
  await expect(page.getByText('Свободных экземпляров: 3', {exact: true})).toHaveCount(0);
});

for (const source of ['points', 'availability']) {
  for (const kind of ['http', 'network', 'contract']) {
    test(`mocked: ${source} ${kind} error, independent retry and preserved product`, async ({page}) => {
      const endpoint = source === 'points' ? '**/api/pickup-points/' : '**/api/products/1/availability/';
      await page.route(endpoint, route => kind === 'network' ? route.abort()
        : route.fulfill(kind === 'http' ? {status: 503, json: {}}
          : {json: source === 'points' ? {results: []} : {...availability, pickup_points: [{pickup_point_id: 11, available_count: -1}]}}));
      await openProduct(page);
      await expect(page.getByRole('alert')).toContainText(source === 'points' ? 'Не удалось загрузить пункты выдачи.' : 'Не удалось загрузить наличие товара.');
      await expect(page.getByText('125,50 ₽/час', {exact: true})).toBeVisible();
      await expect(page.getByText('Нет в наличии', {exact: true})).toHaveCount(0);
      const unrelatedRequests: string[] = [];
      page.on('request', request => {
        if (request.url().endsWith('/api/products/1/') || request.url().endsWith(source === 'points' ? '/availability/' : '/pickup-points/')) unrelatedRequests.push(request.url());
      });
      await page.unroute(endpoint);
      // Restore the original handler too: unroute removes every identical pattern.
      if (source === 'points') await page.route(endpoint, route => route.fulfill({json: pickupPoints}));
      await page.getByRole('button', {name: source === 'points' ? 'Повторить загрузку ПВ' : 'Повторить загрузку наличия'}).click();
      await expect(page.getByRole('alert')).toHaveCount(0);
      await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption('11');
      await expect(page.getByText('Свободных экземпляров: 3', {exact: true})).toBeVisible();
      expect(unrelatedRequests).toEqual([]);
    });
  }
}

for (const source of ['product', 'availability']) {
  for (const fail of [false, true]) {
    test(`mocked: old ${source} ${fail ? 'failure' : 'success'} cannot replace another product`, async ({page}) => {
      let release!: () => void;
      const pending = new Promise<void>(resolve => {release = resolve;});
      const endpoint = source === 'product' ? '**/api/products/1/' : '**/api/products/1/availability/';
      await page.route(endpoint, async route => {await pending; await route.fulfill(fail ? {status: 500, json: {}} : {json: source === 'product' ? product : availability});});
      await page.goto('/catalog');
      const oldRequest = page.waitForRequest(endpoint);
      await page.getByRole('link', {name: product.name, exact: true}).click();
      await oldRequest;
      await page.getByRole('link', {name: '← В каталог'}).click();
      await page.getByRole('link', {name: otherProduct.name, exact: true}).click();
      await expect(page.getByRole('heading', {name: otherProduct.name, exact: true})).toBeVisible();
      await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption('11');
      await expect(page.getByText('Свободных экземпляров: 7', {exact: true})).toBeVisible();
      const oldResponse = page.waitForResponse(endpoint);
      release();
      await (await oldResponse).finished();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await expect(page.getByRole('heading', {name: otherProduct.name, exact: true})).toBeVisible();
      await expect(page.getByText('Свободных экземпляров: 7', {exact: true})).toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);
    });
  }
}

test('mocked: public page survives session errors and scoped CSS preserves catalog and auth', async ({page}) => {
  await page.route('**/api/auth/me/', route => route.abort());
  await page.goto('/catalog');
  const catalogStyles = () => page.locator('header, footer, .product-card, .product-card h2, .catalog-filters select').evaluateAll(elements => elements.map(element => {
    const style = getComputedStyle(element);
    return [style.color, style.backgroundColor, style.fontFamily, style.fontSize, style.borderRadius, style.padding, style.margin];
  }));
  const before = await catalogStyles();
  await page.getByRole('link', {name: product.name, exact: true}).click();
  await expect(page.getByRole('heading', {name: product.name, exact: true})).toBeVisible();
  await page.getByRole('link', {name: '← В каталог'}).click();
  await expect(page.getByRole('article')).toHaveCount(2);
  expect(await catalogStyles()).toEqual(before);
  await page.getByRole('navigation').getByRole('link', {name: 'Войти', exact: true}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить сессию.');
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: false, user: null}}));
  await page.getByRole('button', {name: 'Повторить проверку'}).click();
  await expect(page.getByRole('heading', {name: 'Войти в БЕРИ'})).toBeVisible();
  await page.getByRole('navigation').getByRole('link', {name: 'Регистрация', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Создать аккаунт'})).toBeVisible();
});
