const promisify = (ctx, fn) =>
  function (...args) {
    const result = fn.apply(ctx, [this.name, ...args]);
    return result && result.then ? result : Promise.resolve(result);
  };

export const createPropertyStorage = (name, storage) => ({
  name,
  save: promisify(storage, storage.save),
  load: promisify(storage, storage.load),
});
