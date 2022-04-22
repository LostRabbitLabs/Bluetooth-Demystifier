import React from 'react';
import PropTypes from 'prop-types';

import Typography from '@mui/material/Typography';

import { parseDate } from '../../common/util';

import {
  XYPlot,
  LineSeries,
  XAxis,
  YAxis,
  makeWidthFlexible,
} from 'react-vis';

const FlexibleXYPlot = makeWidthFlexible(XYPlot);

export const RSSIGraph = ({
  rssi
}) => {
  
  const data = rssi.sort((f, l) => {
    return f.id - l.id
  }).map((r) => ({
    x: parseDate(r.time),
    y: (r.rssi * (-1))
  }));

  return (
    <>
      <Typography variant='h6' sx={{ mt: 2, mb: 2 }}>
        RSSI
      </Typography>
      <FlexibleXYPlot
        xType='time'
        height={300}
      >
        <XAxis />
        <YAxis tickFormat={(v) => `-${v}`} />
        <LineSeries
          data={data}
        />
      </FlexibleXYPlot>
    </>
  );
};

RSSIGraph.propTypes = {
  rssi: PropTypes.array,
};

export default RSSIGraph;
