"""Coleções são referências estáveis; nunca movem ou copiam mídia da biblioteca."""
import copy
import math
import random
import time


def target_references(target):
    return target.get('items', []) if target.get('type') == 'shuffle' else [target]


class ShuffleGroups:
    """Estado da sessão; o dispatcher serializa escolha, cooldown e redisparo."""
    def __init__(self, rng=None, clock=None):
        self.rng = rng or random.Random()
        self.clock = clock or time.monotonic
        self.states = {}

    def choose(self, key, items, cooldown_ms=0, repeat_last=False):
        state = self.states.get(key)
        if not state or state['items'] != items:
            state = {'items': copy.deepcopy(items), 'bag': [], 'last': None, 'at': None}
            self.states[key] = state
        now = self.clock()
        if state['at'] is not None and now - state['at'] < cooldown_ms / 1000:
            raise ValueError('Cooldown ativo; aguarde antes de disparar novamente')
        if repeat_last:
            if state['last'] is None:
                raise ValueError('Nenhuma escolha anterior neste grupo')
            chosen = state['last']
        else:
            if not state['bag']:
                state['bag'] = copy.deepcopy(items)
                self.rng.shuffle(state['bag'])
                if len(items) > 1 and state['bag'][-1] == state['last']:
                    state['bag'][0], state['bag'][-1] = state['bag'][-1], state['bag'][0]
            chosen = state['bag'].pop()
        state['last'], state['at'] = copy.deepcopy(chosen), now
        return copy.deepcopy(chosen)

    def snapshot(self):
        return {key: copy.deepcopy(value['last']) for key, value in self.states.items()}


def normalize_binding(value):
    if not isinstance(value, str):
        raise ValueError('Atalho inválido')
    aliases = {'control': 'ctrl', 'ctl': 'ctrl', 'esc': 'escape', 'return': 'enter',
               'windows': 'win', 'command': 'win', 'option': 'alt'}
    parts = [aliases.get(part.strip().lower(), part.strip().lower()) for part in value.split('+')]
    if parts == ['']:
        return ''
    if any(not part or ',' in part for part in parts) or len(parts) != len(set(parts)):
        raise ValueError('Use uma combinação simples de teclas')
    modifiers = ['ctrl', 'alt', 'shift', 'win']
    keys = [p for p in parts if p not in modifiers]
    if len(keys) != 1:
        raise ValueError('Atalho precisa de uma tecla e modificadores opcionais')
    return '+'.join([p for p in modifiers if p in parts] + keys)


def binding_entries(state, collection=None):
    settings = state['settings']
    entries = [(settings.get(key, ''), 'global:' + key) for key in
               ('stop_all_hotkey', 'replay_hotkey', 'overlay_hotkey')]
    entries += [(c.get('hotkey', ''), 'clip:' + c['id']) for c in state['clips'] if not c.get('pending')]
    entries += [(s.get('hotkey', ''), 'sequence:' + s['id']) for s in state['sequences'].values()]
    if collection:
        entries += [(p.get('hotkey', ''), 'pad:' + p['id']) for p in collection['pads']]
    return entries


def binding_conflicts(state, collection=None):
    owners, conflicts = {}, []
    for raw, owner in binding_entries(state, collection):
        try:
            key = normalize_binding(raw)
        except ValueError as exc:
            conflicts.append({'hotkey': raw, 'owners': [owner], 'error': str(exc)})
            continue
        if key:
            owners.setdefault(key, []).append(owner)
    conflicts += [{'hotkey': key, 'owners': values} for key, values in owners.items() if len(values) > 1]
    return conflicts


class CollectionStore:
    def validate_settings_bindings(self, updates):
        keys = {'stop_all_hotkey', 'replay_hotkey', 'overlay_hotkey'} & updates.keys()
        if not keys:
            return
        candidate = self.snapshot()
        candidate['settings'].update(updates)
        for key in keys:
            normalize_binding(candidate['settings'][key] or '')
        owners = {'global:' + key for key in keys}
        for collection in [None, *candidate['collections'].values()]:
            conflicts = [c for c in binding_conflicts(candidate, collection) if owners.intersection(c['owners'])]
            if conflicts:
                raise ValueError('Atalho em conflito: ' + '; '.join(c['hotkey'] + ' (' + ', '.join(c['owners']) + ')' for c in conflicts))

    def validate_binding_change(self, kind, ident, value):
        """Mudança global deve respeitar todas as coleções, inclusive as inativas."""
        normalize_binding(value or '')
        snapshot = self.snapshot()
        if kind == 'global':
            snapshot['settings'][ident] = value
        elif kind == 'sequence':
            snapshot['sequences'].setdefault(ident, {'id': ident})['hotkey'] = value
        else:
            clip = next((c for c in snapshot['clips'] if c['id'] == ident), None)
            if clip is None:
                clip = {'id': ident}; snapshot['clips'].append(clip)
            clip['hotkey'] = value
        for collection in [None, *snapshot['collections'].values()]:
            conflicts = [c for c in binding_conflicts(snapshot, collection) if kind+':'+ident in c['owners']]
            if conflicts:
                raise ValueError('Atalho em conflito: ' + '; '.join(c['hotkey'] + ' (' + ', '.join(c['owners']) + ')' for c in conflicts))

    def get_collection(self, ident):
        with self._lock:
            return copy.deepcopy(self.state['collections'].get(ident))

    def collections_using(self, kind, ident):
        with self._lock:
            return [c['name'] for c in self.state['collections'].values()
                    if any({'type': kind, 'id': ident} in target_references(p['target']) for p in c['pads'])]

    def save_collection(self, document):
        with self._lock:
            if not isinstance(document, dict) or document.get('schema_version') != 1:
                raise ValueError('Versão de coleção inválida')
            for field in ('id', 'name'):
                if not isinstance(document.get(field), str) or not document[field].strip() or len(document[field]) > 200:
                    raise ValueError('Nome e identidade da coleção são obrigatórios')
            columns = document.get('columns')
            pads = document.get('pads')
            if type(columns) is not int or not 1 <= columns <= 8 or not isinstance(pads, list) or len(pads) > 128:
                raise ValueError('Layout inválido: 1–8 colunas e até 128 botões')
            positions, identities = set(), set()
            for pad in pads:
                if not isinstance(pad, dict):
                    raise ValueError('Botão inválido')
                ident, position = pad.get('id'), pad.get('position')
                if not isinstance(ident, str) or not ident or ident in identities:
                    raise ValueError('Identidade do botão duplicada ou inválida')
                if type(position) is not int or not 0 <= position < 128 or position in positions:
                    raise ValueError('Posição fixa inválida ou ocupada')
                identities.add(ident); positions.add(position)
                target = pad.get('target', {})
                if not isinstance(target, dict) or target.get('type') not in ('clip', 'sequence', 'shuffle'):
                    raise ValueError('Referência do botão inválida')
                references = target_references(target)
                if not isinstance(references, list) or not 1 <= len(references) <= 128:
                    raise ValueError('Escolha entre 1 e 128 referências para o grupo')
                seen = set()
                for reference in references:
                    if not isinstance(reference, dict) or reference.get('type') not in ('clip', 'sequence') or not isinstance(reference.get('id'), str):
                        raise ValueError('Referência curada inválida; grupos aninhados não são permitidos')
                    key = (reference['type'], reference['id'])
                    if key in seen:
                        raise ValueError('Referência curada duplicada')
                    seen.add(key)
                    exists = self.get_clip(key[1]) if key[0] == 'clip' else self.state['sequences'].get(key[1])
                    if not exists or exists.get('pending'):
                        raise ValueError('Clipe ou sequência não encontrado')
                cooldown = pad.get('cooldown_ms', 0)
                if type(cooldown) not in (int, float) or not math.isfinite(cooldown) or not 0 <= cooldown <= 60000:
                    raise ValueError('Cooldown deve ser de 0 a 60000 ms')
                if pad.get('group') not in ('speech', 'track', 'effect') or pad.get('retrigger') not in ('restart', 'stop', 'ignore'):
                    raise ValueError('Grupo ou regra de redisparo inválida')
                if type(pad.get('hold')) is not bool or type(pad.get('loop')) is not bool:
                    raise ValueError('Segurar e loop devem ser booleanos')
                fade = pad.get('fade_ms', 0)
                if type(fade) not in (int, float) or not math.isfinite(fade) or not 0 <= fade <= 10000:
                    raise ValueError('Saída suave deve ser de 0 a 10000 ms')
                if not isinstance(pad.get('label'), str) or len(pad['label']) > 100:
                    raise ValueError('Rótulo inválido')
                normalize_binding(pad.get('hotkey', ''))
            conflicts = binding_conflicts(self.state, document)
            if conflicts:
                raise ValueError('Atalho em conflito: ' + '; '.join(c['hotkey'] + ' (' + ', '.join(c['owners']) + ')' for c in conflicts))
            previous = self.state['collections'].get(document['id'])
            if previous and document.get('revision') != previous['revision']:
                raise ValueError('A coleção mudou; recarregue antes de salvar')
            saved = copy.deepcopy(document)
            saved['revision'] = (previous or {}).get('revision', 0) + 1
            self.state['collections'][saved['id']] = saved
            try:
                self.save()
            except Exception:
                if previous is None:
                    self.state['collections'].pop(saved['id'])
                else:
                    self.state['collections'][saved['id']] = previous
                raise
            return copy.deepcopy(saved)

    def delete_collection(self, ident):
        with self._lock:
            previous = self.state['collections'].pop(ident, None)
            active = self.state['settings'].get('active_collection_id')
            if active == ident:
                self.state['settings']['active_collection_id'] = None
            try:
                self.save()
            except Exception:
                if previous:
                    self.state['collections'][ident] = previous
                self.state['settings']['active_collection_id'] = active
                raise
            return previous is not None
