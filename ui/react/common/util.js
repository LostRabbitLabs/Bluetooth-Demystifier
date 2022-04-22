import { DateTime } from 'luxon';

/**
 * parseDate
 * 
 * Parses a presumed UTC date to localTime
 * 
 * @param {*} timestamp - SQL Like 
 * @returns Date
 */
export const parseDate = (timestamp) => (
  DateTime.fromSQL(timestamp, { zone: 'UTC', keepLocalTime: true }).toJSDate()
);

/**
 * uuidFilter
 * 
 * Checks if the supplied input matches any uuid or its
 * description
 * 
 * @param {string} filter - filter text
 * @param {object} mac - Mac Object
 * @return Boolean
 */
export const uuidFilter = (f, m) => {
  const uuids = m.uuid.map((u) => u.uuid).filter((u) => (
    u.indexOf(f) > -1
    || u.toLowerCase().indexOf(f) > -1
  ));
  const attrs = m.uuid.map((u) => u.lookup?.attribute?.toLowerCase()).filter((a) => (
    a && a.indexOf(f) > -1
  ));

  return uuids.length > 0 || attrs.length > 0
};
