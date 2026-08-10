import { renderNetworkPage } from './network.js';

export function render(api, store) {
    return renderNetworkPage(api, store, 'wireless');
}
