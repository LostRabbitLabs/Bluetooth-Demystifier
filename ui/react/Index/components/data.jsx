import React from 'react';
import PropTypes from 'prop-types';

import Typography from '@mui/material/Typography';

import { parseDate } from '../../common/util';

export const DataList = ({
  data
}) => {
  return (
    <>
      <Typography variant='h6' sx={{ mt: 2, mb: 2 }}>
        Data
      </Typography>
      <div style={{ maxHeight: 300, overflowY: 'scroll' }}>
        <Typography variant='body'>
          {data.map((d) => (
            <React.Fragment key={d.id}>
              {parseDate(d.time).toLocaleString()} {d.key}:{d.value}
              <br/>
            </React.Fragment>
          ))}
        </Typography>
      </div>
    </>
  );
};

DataList.propTypes = {
  data: PropTypes.array,
};

export default DataList;
